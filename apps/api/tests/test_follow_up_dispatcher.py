"""Tests for apps.api.workers.follow_up_dispatcher.

Covers the channel-matching bug fix (a rule's `channel` was never actually
used to filter which leads it targets — it silently fired for every lead
matching the trigger status regardless of channel, then the free-text/call
send path skipped anything that wasn't the intended channel) and the new
automated-call path for voice-channel leads.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.db.models import FollowUpRule, FollowUpTask, Lead, Org, User
from apps.api.workers import follow_up_dispatcher

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture(autouse=True)
async def _redirect_dispatcher_sessions(test_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """The dispatcher's internal functions open their own AsyncSessionLocal()
    rather than taking a `db` argument — point that at the test engine so it
    shares the same in-memory SQLite the `db_session` fixture writes through
    (same pattern as test_campaign_dialer.py)."""
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(follow_up_dispatcher, "AsyncSessionLocal", session_factory)


async def _seed_org(db: AsyncSession) -> Org:
    org = Org(id=ORG_ID, name="Test Org")
    db.add(org)
    await db.commit()
    return org


async def _seed_lead(db: AsyncSession, *, channel: str, status: str = "contacted", phone: str) -> Lead:
    user = User(org_id=ORG_ID, phone=phone)
    db.add(user)
    await db.flush()
    lead = Lead(org_id=ORG_ID, user_id=user.id, phone=phone, channel=channel, status=status)
    db.add(lead)
    await db.commit()
    return lead


async def _seed_rule(db: AsyncSession, *, channel: str, status: str = "contacted", **kwargs) -> FollowUpRule:
    rule = FollowUpRule(
        org_id=ORG_ID,
        name="Test rule",
        trigger_type="status_change",
        trigger_config={"status": status, "delay_hours": 0},
        channel=channel,
        active=True,
        **kwargs,
    )
    db.add(rule)
    await db.commit()
    return rule


async def test_materialize_rule_tasks_only_matches_leads_on_the_rules_channel(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    whatsapp_lead = await _seed_lead(db_session, channel="whatsapp", phone="+910000000001")
    voice_lead = await _seed_lead(db_session, channel="voice", phone="+910000000002")
    rule = await _seed_rule(db_session, channel="whatsapp", message_template="hi")

    await follow_up_dispatcher._materialize_rule_tasks()

    tasks = (await db_session.execute(select(FollowUpTask).where(FollowUpTask.rule_id == rule.id))).scalars().all()
    matched_lead_ids = {t.lead_id for t in tasks}
    assert matched_lead_ids == {whatsapp_lead.id}
    assert voice_lead.id not in matched_lead_ids


async def test_materialize_rule_tasks_voice_channel_only_matches_voice_leads(
    db_session: AsyncSession,
) -> None:
    await _seed_org(db_session)
    whatsapp_lead = await _seed_lead(db_session, channel="whatsapp", phone="+910000000003")
    voice_lead = await _seed_lead(db_session, channel="voice", phone="+910000000004")
    rule = await _seed_rule(db_session, channel="voice")

    await follow_up_dispatcher._materialize_rule_tasks()

    tasks = (await db_session.execute(select(FollowUpTask).where(FollowUpTask.rule_id == rule.id))).scalars().all()
    matched_lead_ids = {t.lead_id for t in tasks}
    assert matched_lead_ids == {voice_lead.id}
    assert whatsapp_lead.id not in matched_lead_ids


async def test_materialize_rule_tasks_template_rule_ignores_channel(
    db_session: AsyncSession,
) -> None:
    """A template send reaches the recipient via WhatsApp regardless of the
    lead's own channel (see _execute_task), so a template-based rule should
    still match every lead at the target status, not just one channel."""
    await _seed_org(db_session)
    whatsapp_lead = await _seed_lead(db_session, channel="whatsapp", phone="+910000000005")
    voice_lead = await _seed_lead(db_session, channel="voice", phone="+910000000006")
    rule = await _seed_rule(
        db_session, channel="whatsapp", template_name="reminder", template_language="en_US"
    )

    await follow_up_dispatcher._materialize_rule_tasks()

    tasks = (await db_session.execute(select(FollowUpTask).where(FollowUpTask.rule_id == rule.id))).scalars().all()
    matched_lead_ids = {t.lead_id for t in tasks}
    assert matched_lead_ids == {whatsapp_lead.id, voice_lead.id}


async def _seed_sending_task(db: AsyncSession, lead: Lead) -> FollowUpTask:
    task = FollowUpTask(
        org_id=ORG_ID,
        lead_id=lead.id,
        rule_id=None,
        run_at=datetime.now(UTC) - timedelta(minutes=1),
        status="sending",
    )
    db.add(task)
    await db.commit()
    return task


async def test_execute_task_places_call_for_voice_lead(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    lead = await _seed_lead(db_session, channel="voice", phone="+910000000007")
    task = await _seed_sending_task(db_session, lead)

    calls: list[tuple[str, str]] = []

    async def fake_initiate_call(to_e164, answer_url, **kwargs):
        calls.append((to_e164, answer_url))
        return {"request_uuid": "abc"}, "plivo"

    monkeypatch.setattr(follow_up_dispatcher.voice_failover, "is_configured", lambda: True)
    monkeypatch.setattr(follow_up_dispatcher.voice_failover, "initiate_call", fake_initiate_call)

    await follow_up_dispatcher._execute_task(task.id)

    await db_session.refresh(task)
    assert task.status == "sent"
    assert task.sent_at is not None
    assert len(calls) == 1
    to_e164, answer_url = calls[0]
    assert to_e164 == "+910000000007"
    assert f"org_id={ORG_ID}" in answer_url


async def test_execute_task_skips_voice_lead_when_no_voice_provider_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    lead = await _seed_lead(db_session, channel="voice", phone="+910000000008")
    task = await _seed_sending_task(db_session, lead)

    monkeypatch.setattr(follow_up_dispatcher.voice_failover, "is_configured", lambda: False)

    await follow_up_dispatcher._execute_task(task.id)

    await db_session.refresh(task)
    assert task.status == "skipped"


async def test_execute_task_skips_voice_lead_over_call_minute_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    lead = await _seed_lead(db_session, channel="voice", phone="+910000000009")
    task = await _seed_sending_task(db_session, lead)

    called = False

    async def fake_initiate_call(*args, **kwargs):
        nonlocal called
        called = True
        return {}, "plivo"

    async def fake_is_over_plan_limit(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(follow_up_dispatcher.voice_failover, "is_configured", lambda: True)
    monkeypatch.setattr(follow_up_dispatcher.voice_failover, "initiate_call", fake_initiate_call)
    monkeypatch.setattr(follow_up_dispatcher, "is_over_plan_limit", fake_is_over_plan_limit)

    await follow_up_dispatcher._execute_task(task.id)

    await db_session.refresh(task)
    assert task.status == "skipped"
    assert called is False
