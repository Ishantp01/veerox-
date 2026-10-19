"""Scenario Builder back end: draft, publish, rollback, audit.

* There is at most one **draft** per org; only a draft can be edited.
* **Publishing** validates the draft, runs the regression gate (below), numbers
  it ``max(published) + 1`` and makes it immutable. New conversations use it;
  running ones keep the version they started with (``config.resolve_config``).
* **Rollback** publishes a *copy* of an older version as a new version, so the
  history is never rewritten and the audit trail stays complete.
* Every change writes an ``AuditEvent`` in the same transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.conversation.config import (
    EngineConfig,
    config_from_json,
    default_config,
    invalidate_active,
    validate_config_payload,
)
from apps.api.core.conversation.understand import classify_with_rules
from apps.api.db.models.conversation_engine import AuditEvent, ConversationConfigVersion


class BuilderError(Exception):
    """A rejected builder action; ``problems`` are shown to the admin as-is."""

    def __init__(self, message: str, problems: list[str] | None = None, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.problems = problems or []
        self.status = status


# Phrases whose meaning is already validated in production. A new configuration
# must not change what they resolve to ("a new configuration should not be
# published if it breaks previously validated conversation paths").
GOLDEN_PHRASES: tuple[tuple[str, str], ...] = (
    ("I am busy, call me later", "busy_callback"),
    ("I am not interested", "not_interested"),
    ("I want to talk to a human", "human_handoff"),
    ("this is useless, worst service ever", "angry"),
    ("what is the price", "price_question"),
    ("please send the details on whatsapp", "whatsapp_info_request"),
    ("I want to book a demo tomorrow at 5 pm", "appointment_request"),
    ("thank you, bye", "closing"),
    ("that is too expensive", "objection"),
    ("hello", "greeting"),
)

# These two are handled identically; resolving to either is not a regression.
_EQUIVALENT = {frozenset({"busy_callback", "callback_request"})}


def _same(a: str, b: str) -> bool:
    return a == b or frozenset({a, b}) in _EQUIVALENT


def regression_problems(config: EngineConfig) -> list[str]:
    """Problems that must block publishing; empty when the config is safe."""
    problems: list[str] = []
    for phrase, expected in GOLDEN_PHRASES:
        if not config.is_enabled(expected):
            continue  # the admin deliberately switched this scenario off
        result = classify_with_rules(phrase, state=None, config=config)
        got = result.scenario if result is not None else None
        if got is None or not _same(got, expected):
            problems.append(
                f"'{phrase}' used to mean '{expected}' but now resolves to '{got or 'nothing'}'"
            )
    for scenario in config.scenarios.values():
        if not scenario.enabled:
            continue
        for example in scenario.examples:
            result = classify_with_rules(example, state=None, config=config)
            if result is not None and not _same(result.scenario, scenario.id):
                problems.append(
                    f"Example '{example}' of '{scenario.id}' is understood as '{result.scenario}'; "
                    "make it more specific or move it"
                )
    return problems


def add_audit(
    db: AsyncSession,
    org_id: UUID,
    actor: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Queue an audit event on the caller's transaction (no commit here)."""
    db.add(
        AuditEvent(
            org_id=org_id,
            actor=actor[:120],
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


async def get_draft(db: AsyncSession, org_id: UUID) -> ConversationConfigVersion | None:
    return (
        await db.execute(
            select(ConversationConfigVersion).where(
                ConversationConfigVersion.org_id == org_id,
                ConversationConfigVersion.status == "draft",
            )
        )
    ).scalar_one_or_none()


async def get_active_row(db: AsyncSession, org_id: UUID) -> ConversationConfigVersion | None:
    return (
        await db.execute(
            select(ConversationConfigVersion)
            .where(
                ConversationConfigVersion.org_id == org_id,
                ConversationConfigVersion.status == "published",
            )
            .order_by(ConversationConfigVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_version(
    db: AsyncSession, org_id: UUID, version: int
) -> ConversationConfigVersion | None:
    return (
        await db.execute(
            select(ConversationConfigVersion).where(
                ConversationConfigVersion.org_id == org_id,
                ConversationConfigVersion.version == version,
                ConversationConfigVersion.status == "published",
            )
        )
    ).scalar_one_or_none()


def full_config_json(config: EngineConfig) -> dict[str, Any]:
    """The complete scenario list (built-ins included) as stored JSON."""
    return {
        "scenarios": [s.to_json() for s in config.scenarios.values()],
        "settings": {
            "low_confidence_action": config.low_confidence_action,
            # Only present when switched on, so the stored shape is unchanged for every other org.
            **({"enhanced_dialogue": True} if config.enhanced_dialogue else {}),
        },
    }


async def starting_config(db: AsyncSession, org_id: UUID) -> dict[str, Any]:
    """What a new draft starts from: the active version, else the built-ins."""
    active = await get_active_row(db, org_id)
    config = (
        config_from_json(active.config, version_id=active.id, version=active.version or 0)
        if active is not None
        else default_config()
    )
    return full_config_json(config)


async def save_draft(
    db: AsyncSession,
    org_id: UUID,
    actor: str,
    payload: dict[str, Any],
    note: str | None,
) -> ConversationConfigVersion:
    problems = validate_config_payload(payload)
    if problems:
        raise BuilderError("The configuration is not valid", problems)
    # Store the normalised form so the stored draft is always loadable.
    normalised = full_config_json(config_from_json(payload))
    draft = await get_draft(db, org_id)
    created = draft is None
    if draft is None:
        draft = ConversationConfigVersion(
            org_id=org_id,
            status="draft",
            config=normalised,
            note=note,
            created_by=actor[:120],
        )
        db.add(draft)
    else:
        draft.config = normalised
        draft.note = note
    await db.flush()
    add_audit(
        db,
        org_id,
        actor,
        "config.draft_created" if created else "config.draft_saved",
        "config_draft",
        str(draft.id),
        {"scenarios": len(normalised["scenarios"])},
    )
    await db.commit()
    await db.refresh(draft)
    return draft


async def discard_draft(db: AsyncSession, org_id: UUID, actor: str) -> None:
    draft = await get_draft(db, org_id)
    if draft is None:
        raise BuilderError("There is no draft to discard", status=404)
    add_audit(db, org_id, actor, "config.draft_discarded", "config_draft", str(draft.id))
    await db.delete(draft)
    await db.commit()


async def _next_version(db: AsyncSession, org_id: UUID) -> int:
    current = await db.scalar(
        select(func.max(ConversationConfigVersion.version)).where(
            ConversationConfigVersion.org_id == org_id,
            ConversationConfigVersion.status == "published",
        )
    )
    return (current or 0) + 1


async def publish_draft(
    db: AsyncSession, org_id: UUID, actor: str, note: str | None
) -> ConversationConfigVersion:
    draft = await get_draft(db, org_id)
    if draft is None:
        raise BuilderError("There is no draft to publish", status=404)
    problems = validate_config_payload(draft.config)
    if problems:
        raise BuilderError("The draft is not valid", problems)
    regression = regression_problems(config_from_json(draft.config))
    if regression:
        raise BuilderError(
            "Publishing is blocked: this change would break conversation paths that already work",
            regression,
        )
    version = await _next_version(db, org_id)
    draft.status = "published"
    draft.version = version
    draft.published_by = actor[:120]
    draft.published_at = datetime.now(UTC)
    if note:
        draft.note = note
    add_audit(
        db,
        org_id,
        actor,
        "config.published",
        "config_version",
        str(draft.id),
        {"version": version, "note": draft.note},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise BuilderError("Another publish happened at the same time; try again", status=409) from None
    invalidate_active(org_id)
    await db.refresh(draft)
    return draft


async def rollback_to(
    db: AsyncSession, org_id: UUID, actor: str, version: int, note: str | None
) -> ConversationConfigVersion:
    target = await get_version(db, org_id, version)
    if target is None:
        raise BuilderError(f"Version {version} does not exist", status=404)
    active = await get_active_row(db, org_id)
    if active is not None and active.version == version:
        raise BuilderError(f"Version {version} is already active", status=409)
    new_version = await _next_version(db, org_id)
    copy = ConversationConfigVersion(
        org_id=org_id,
        status="published",
        version=new_version,
        config=target.config,
        note=note or f"Rollback to version {version}",
        rolled_back_from=version,
        created_by=actor[:120],
        published_by=actor[:120],
        published_at=datetime.now(UTC),
    )
    db.add(copy)
    await db.flush()
    add_audit(
        db,
        org_id,
        actor,
        "config.rolled_back",
        "config_version",
        str(copy.id),
        {"from_version": version, "new_version": new_version},
    )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise BuilderError("Another publish happened at the same time; try again", status=409) from None
    invalidate_active(org_id)
    await db.refresh(copy)
    return copy
