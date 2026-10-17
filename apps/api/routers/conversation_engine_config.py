"""Scenario Builder, simulator, audit log and metrics for the Conversation Engine.

Same prefix and access rules as ``conversation_engine.py`` (org admins,
``X-Admin-Token`` and platform superusers; plain members get 403). All logic
lives in ``core/conversation/{builder,simulate,metrics}.py``; these routes only
validate input and translate errors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.api.core.conversation import builder
from apps.api.core.conversation.config import (
    DEFAULT_SCENARIOS,
    config_from_json,
    default_config,
    load_active_config,
    load_version_config,
    validate_config_payload,
)
from apps.api.core.conversation.metrics import compute_metrics
from apps.api.core.conversation.script_import import suggest_from_script
from apps.api.core.conversation.simulate import simulate_turn
from apps.api.core.llm import chat_completion
from apps.api.core.org_openai_key import resolve_openai_api_key
from apps.api.db.models.conversation_engine import (
    AuditEvent,
    ConversationConfigVersion,
    ConversationScenarioTemplate,
)
from apps.api.db.models.org import Org
from apps.api.db.models.script import Script
from apps.api.deps import (
    DbDep,
    MemberScopeDep,
    RequestOrgDep,
    SessionPayloadDep,
    get_current_user,
    require_feature,
    verify_admin_or_session,
    verify_platform_admin,
)


def _require_org_manager(member_scope: MemberScopeDep) -> None:
    if member_scope is not None:
        raise HTTPException(status_code=403, detail="Insufficient role")


async def _resolve_actor(
    db: DbDep,
    payload: SessionPayloadDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> str:
    """Who is making this change, for the audit trail."""
    try:
        user = await get_current_user(db, payload, x_admin_token)
    except HTTPException:
        return "admin-token" if x_admin_token else "unknown"
    return (user.email or str(user.id))[:120]


ActorDep = Annotated[str, Depends(_resolve_actor)]

router = APIRouter(
    prefix="/conversation-engine",
    tags=["conversation-engine"],
    dependencies=[
        Depends(verify_admin_or_session),
        Depends(_require_org_manager),
        Depends(require_feature("conversation_engine")),
    ],
)


def _builder_http_error(exc: builder.BuilderError) -> HTTPException:
    detail = exc.message
    if exc.problems:
        detail += ":\n" + "\n".join(f"- {p}" for p in exc.problems)
    return HTTPException(status_code=exc.status, detail=detail)


# ---------------------------------------------------------------------------
# Scenario Builder: versions, draft, publish, rollback
# ---------------------------------------------------------------------------


class ScenarioIn(BaseModel):
    id: str = Field(min_length=2, max_length=31)
    name: str = Field(min_length=1, max_length=80)
    situation: str = Field(default="", max_length=500)
    examples: list[str] = Field(default_factory=list, max_length=40)
    goal: str = Field(default="", max_length=500)
    should_do: str = Field(default="", max_length=800)
    must_avoid: str = Field(default="", max_length=800)
    actions: list[str] = Field(default_factory=lambda: ["none"], max_length=8)
    next_scenarios: list[str] = Field(default_factory=list, max_length=10)
    enabled: bool = True
    reply_example: str = Field(default="", max_length=500)


class ConfigSettingsIn(BaseModel):
    # What the AI does when it is not sure what the customer meant (blueprint section 8).
    low_confidence_action: Literal["script", "clarify", "handoff"] = "script"


class DraftIn(BaseModel):
    scenarios: list[ScenarioIn] = Field(max_length=60)
    settings: ConfigSettingsIn = Field(default_factory=ConfigSettingsIn)
    note: str | None = Field(default=None, max_length=300)


class PublishIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


class RollbackIn(BaseModel):
    version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=300)


class ConfigVersionOut(BaseModel):
    id: UUID
    version: int | None
    status: str
    note: str | None
    rolled_back_from: int | None
    created_by: str | None
    published_by: str | None
    created_at: datetime
    published_at: datetime | None
    scenario_count: int
    scenarios: list[dict[str, Any]] | None = None
    settings: dict[str, str] = Field(default_factory=dict)


def _version_out(row: ConversationConfigVersion, *, with_scenarios: bool) -> ConfigVersionOut:
    config = config_from_json(row.config, version_id=row.id, version=row.version or 0)
    return ConfigVersionOut(
        id=row.id,
        version=row.version,
        status=row.status,
        note=row.note,
        rolled_back_from=row.rolled_back_from,
        created_by=row.created_by,
        published_by=row.published_by,
        created_at=row.created_at,
        published_at=row.published_at,
        scenario_count=len(config.scenarios),
        scenarios=[s.to_json() for s in config.scenarios.values()] if with_scenarios else None,
        settings={"low_confidence_action": config.low_confidence_action},
    )


class ConfigOverviewOut(BaseModel):
    active: ConfigVersionOut | None
    draft: ConfigVersionOut | None
    # Complete scenario list a new draft starts from (active version or built-ins).
    base: list[dict[str, Any]]
    base_settings: dict[str, str] = Field(default_factory=dict)


@router.get("/scenarios/library", response_model=list[dict[str, Any]])
async def scenario_library() -> list[dict[str, Any]]:
    """The built-in scenario library: what an org has before any customisation."""
    return [s.to_json() for s in DEFAULT_SCENARIOS]


@router.get("/config", response_model=ConfigOverviewOut)
async def get_config(db: DbDep, org_id: RequestOrgDep) -> ConfigOverviewOut:
    active = await builder.get_active_row(db, org_id)
    draft = await builder.get_draft(db, org_id)
    base = await builder.starting_config(db, org_id)
    return ConfigOverviewOut(
        active=_version_out(active, with_scenarios=True) if active else None,
        draft=_version_out(draft, with_scenarios=True) if draft else None,
        base=base["scenarios"],
        base_settings=base["settings"],
    )


@router.get("/config/versions", response_model=list[ConfigVersionOut])
async def list_config_versions(db: DbDep, org_id: RequestOrgDep) -> list[ConfigVersionOut]:
    rows = (
        (
            await db.execute(
                select(ConversationConfigVersion)
                .where(
                    ConversationConfigVersion.org_id == org_id,
                    ConversationConfigVersion.status == "published",
                )
                .order_by(ConversationConfigVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_version_out(row, with_scenarios=False) for row in rows]


@router.get("/config/versions/{version}", response_model=ConfigVersionOut)
async def get_config_version(version: int, db: DbDep, org_id: RequestOrgDep) -> ConfigVersionOut:
    row = await builder.get_version(db, org_id, version)
    if row is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(row, with_scenarios=True)


class CheckOut(BaseModel):
    valid: bool
    # Structural problems (bad id, unknown action, missing goal ...).
    problems: list[str]
    # Existing conversation paths this change would break; blocks publishing.
    regression: list[str]


@router.post("/config/check", response_model=CheckOut)
async def check_config(payload: DraftIn) -> CheckOut:
    """Validate a configuration without saving it, so the builder can show
    every problem next to the form before an admin saves or publishes."""
    data = {
        "scenarios": [s.model_dump() for s in payload.scenarios],
        "settings": payload.settings.model_dump(),
    }
    problems = builder.validate_config_payload(data)
    regression = [] if problems else builder.regression_problems(config_from_json(data))
    return CheckOut(valid=not problems, problems=problems, regression=regression)


@router.put("/config/draft", response_model=ConfigVersionOut)
async def save_config_draft(
    payload: DraftIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> ConfigVersionOut:
    try:
        draft = await builder.save_draft(
            db,
            org_id,
            actor,
            {
                "scenarios": [s.model_dump() for s in payload.scenarios],
                "settings": payload.settings.model_dump(),
            },
            payload.note,
        )
    except builder.BuilderError as exc:
        raise _builder_http_error(exc) from None
    return _version_out(draft, with_scenarios=True)


@router.delete("/config/draft", status_code=204)
async def discard_config_draft(db: DbDep, org_id: RequestOrgDep, actor: ActorDep) -> None:
    try:
        await builder.discard_draft(db, org_id, actor)
    except builder.BuilderError as exc:
        raise _builder_http_error(exc) from None


@router.post("/config/draft/publish", response_model=ConfigVersionOut)
async def publish_config_draft(
    payload: PublishIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> ConfigVersionOut:
    try:
        row = await builder.publish_draft(db, org_id, actor, payload.note)
    except builder.BuilderError as exc:
        raise _builder_http_error(exc) from None
    return _version_out(row, with_scenarios=False)


@router.post("/config/rollback", response_model=ConfigVersionOut)
async def rollback_config(
    payload: RollbackIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> ConfigVersionOut:
    try:
        row = await builder.rollback_to(db, org_id, actor, payload.version, payload.note)
    except builder.BuilderError as exc:
        raise _builder_http_error(exc) from None
    return _version_out(row, with_scenarios=False)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class SimulateIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # "draft" (falls back to active when there is none), "active", or a number.
    source: str = Field(default="draft", max_length=12)
    state: str | None = Field(default=None, max_length=32)
    memory: dict[str, str] = Field(default_factory=dict)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    use_llm: bool = True
    channel: Literal["whatsapp", "voice"] = "whatsapp"


@router.post("/simulate")
async def simulate(payload: SimulateIn, db: DbDep, org_id: RequestOrgDep) -> dict[str, Any]:
    org = await db.get(Org, org_id)
    if payload.source == "draft":
        draft = await builder.get_draft(db, org_id)
        config = (
            config_from_json(draft.config, version_id=draft.id, version=0)
            if draft is not None
            else await load_active_config(db, org_id)
        )
        used = "draft" if draft is not None else "active"
    elif payload.source == "active":
        config = await load_active_config(db, org_id)
        used = "active"
    else:
        try:
            number = int(payload.source)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="source must be draft, active or a version number"
            ) from None
        row = await builder.get_version(db, org_id, number)
        if row is None:
            raise HTTPException(status_code=404, detail="Version not found")
        loaded = await load_version_config(db, org_id, row.id)
        config = loaded if loaded is not None else default_config()
        used = f"version {number}"
    memory = {str(k)[:32]: str(v)[:200] for k, v in list(payload.memory.items())[:20]}
    history = [
        {"role": m.get("role", ""), "content": m.get("content", "")[:500]}
        for m in payload.history
        if m.get("role") in ("user", "assistant")
    ]
    result = await simulate_turn(
        db,
        org_id,
        text=payload.text,
        config=config,
        state=payload.state,
        memory=memory,
        history=history,
        use_llm=payload.use_llm,
        api_key=resolve_openai_api_key(org),
        smart_script=bool(org and org.conversation_mode == "smart_script"),
        channel=payload.channel,
    )
    result["config_source"] = used
    return result


# ---------------------------------------------------------------------------
# Audit log and metrics
# ---------------------------------------------------------------------------


class AuditEventOut(BaseModel):
    id: UUID
    actor: str
    action: str
    target_type: str
    target_id: str | None
    detail: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/audit", response_model=list[AuditEventOut])
async def list_audit(
    db: DbDep,
    org_id: RequestOrgDep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.org_id == org_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/metrics")
async def engine_metrics(
    db: DbDep,
    org_id: RequestOrgDep,
    days: int = Query(7, ge=1, le=90),
    channel: Annotated[Literal["whatsapp", "voice"] | None, Query()] = None,
) -> dict[str, Any]:
    return await compute_metrics(db, org_id, days=days, channel=channel)


# ---------------------------------------------------------------------------
# Script -> structured suggestions (blueprint section 13). Read-only: nothing
# is saved here and the original script is never touched.
# ---------------------------------------------------------------------------


class ScriptOptionOut(BaseModel):
    id: UUID
    name: str
    channel: str
    is_default: bool

    model_config = {"from_attributes": True}


class ScriptStructureIn(BaseModel):
    script_id: UUID | None = None
    text: str | None = Field(default=None, max_length=20_000)
    use_llm: bool = True


@router.get("/scripts", response_model=list[ScriptOptionOut])
async def list_scripts(db: DbDep, org_id: RequestOrgDep) -> list[Script]:
    rows = await db.execute(
        select(Script).where(Script.org_id == org_id).order_by(Script.channel, Script.name)
    )
    return list(rows.scalars().all())


@router.post("/script/structure")
async def structure_script(
    payload: ScriptStructureIn, db: DbDep, org_id: RequestOrgDep
) -> dict[str, Any]:
    """Suggest approved knowledge and scenarios from an existing script."""
    if payload.script_id is not None:
        script = await db.get(Script, payload.script_id)
        if script is None or script.org_id != org_id:
            raise HTTPException(status_code=404, detail="Script not found")
        text = script.content
        source = script.name
    elif payload.text and payload.text.strip():
        text = payload.text
        source = "pasted text"
    else:
        raise HTTPException(status_code=422, detail="Pick a script or paste its text")

    llm = None
    if payload.use_llm:
        org = await db.get(Org, org_id)
        api_key = resolve_openai_api_key(org)

        async def llm(messages: list[dict[str, Any]]):  # type: ignore[misc]
            return await chat_completion(messages, temperature=0.0, max_tokens=2500, api_key=api_key)

    active = await load_active_config(db, org_id)
    suggestions = await suggest_from_script(
        text, existing_scenario_ids=set(active.ids()), llm=llm
    )
    return {"source": source, "characters": len(text), **suggestions.to_json()}


# ---------------------------------------------------------------------------
# Platform-wide scenario templates (blueprint section 12). Any org admin can read
# the library and copy a template into their own draft; only platform admins can
# change it. Copies are independent: editing a template never alters an org.
# ---------------------------------------------------------------------------


class TemplateOut(BaseModel):
    scenario_id: str
    definition: dict[str, Any]
    updated_by: str
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(db: DbDep) -> list[ConversationScenarioTemplate]:
    rows = await db.execute(
        select(ConversationScenarioTemplate).order_by(ConversationScenarioTemplate.scenario_id)
    )
    return list(rows.scalars().all())


@router.put(
    "/templates/{scenario_id}",
    response_model=TemplateOut,
    dependencies=[Depends(verify_platform_admin)],
)
async def upsert_template(
    scenario_id: str, payload: ScenarioIn, db: DbDep, org_id: RequestOrgDep, actor: ActorDep
) -> ConversationScenarioTemplate:
    if payload.id != scenario_id:
        raise HTTPException(status_code=422, detail="The scenario id in the body must match the URL")
    if scenario_id in {s.id for s in DEFAULT_SCENARIOS}:
        raise HTTPException(status_code=422, detail="Built-in scenarios cannot be templates")
    definition = payload.model_dump()
    problems = validate_config_payload({"scenarios": [definition]})
    if problems:
        raise HTTPException(status_code=422, detail="; ".join(problems))
    row = (
        await db.execute(
            select(ConversationScenarioTemplate).where(
                ConversationScenarioTemplate.scenario_id == scenario_id
            )
        )
    ).scalar_one_or_none()
    created = row is None
    if row is None:
        row = ConversationScenarioTemplate(scenario_id=scenario_id, definition=definition, updated_by=actor[:120])
        db.add(row)
    else:
        row.definition = definition
        row.updated_by = actor[:120]
    builder.add_audit(
        db, org_id, actor, "template.created" if created else "template.updated",
        "template", scenario_id, {"name": payload.name},
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/templates/{scenario_id}",
    status_code=204,
    dependencies=[Depends(verify_platform_admin)],
)
async def delete_template(scenario_id: str, db: DbDep, org_id: RequestOrgDep, actor: ActorDep) -> None:
    row = (
        await db.execute(
            select(ConversationScenarioTemplate).where(
                ConversationScenarioTemplate.scenario_id == scenario_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    builder.add_audit(db, org_id, actor, "template.deleted", "template", scenario_id)
    await db.delete(row)
    await db.commit()
