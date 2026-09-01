"""Tests for realtime_bridge.py's `_system_instructions` — which system
prompt a live voice call actually gets.

Written to check a specific report: "the org's custom script gets lost" on
campaign calls. Root cause confirmed by reading `_system_instructions`: a
campaign call used to take an early-return branch that replaced the org's
own script outright with a fixed, unrelated qualification template — an
org's custom script was never used on a campaign call at all, regardless of
concurrency. Fixed so the org's script (or the platform default, same
precedence as any other call) is always the base, with the campaign's
qualification criteria layered on top as an addendum instead of a
replacement (see `campaign_qualification_append`).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import realtime_bridge as bridge_module
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT
from apps.api.db.models import CallCampaign, CampaignTarget, Org

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def reuse_db(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """Make realtime_bridge.py's own `AsyncSessionLocal()` calls hit the
    test session, same pattern as test_voice_transfer.py's `reuse_db`."""

    class _Ctx:
        async def __aenter__(self) -> AsyncSession:
            return db_session

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge_module, "AsyncSessionLocal", lambda: _Ctx())
    return db_session


async def test_campaign_call_uses_org_script_as_base_plus_criteria(
    reuse_db: AsyncSession,
) -> None:
    """The exact bug report, now fixed: an org has a custom script
    configured — a campaign call for that same org must use it as the base,
    with the campaign's own `criteria` layered on top as an addendum, not
    swapped out for a generic unrelated template."""
    org = Org(
        id=ORG_ID,
        name="Test Org",
        script="MY CUSTOM SCRIPT: always mention our 20% launch discount.",
    )
    reuse_db.add(org)
    campaign = CallCampaign(
        org_id=ORG_ID, name="Spring campaign", criteria="Wants a demo this quarter"
    )
    reuse_db.add(campaign)
    await reuse_db.flush()
    target = CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone="+14155551111")
    reuse_db.add(target)
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(target.id, ORG_ID)

    assert "MY CUSTOM SCRIPT" in instructions
    assert "20% launch discount" in instructions
    assert "Wants a demo this quarter" in instructions
    # The base script's own text must come before the qualification
    # addendum — it's the persona/flow the addendum layers on top of.
    assert instructions.index("MY CUSTOM SCRIPT") < instructions.index("Wants a demo this quarter")


async def test_campaign_call_falls_back_to_platform_default_with_no_org_script(
    reuse_db: AsyncSession,
) -> None:
    """No custom script set — a campaign call still needs a base persona/
    flow, so it falls back to the same platform default any other call
    without a script would use, with criteria layered on top exactly as
    when a script IS set."""
    org = Org(id=ORG_ID, name="Test Org")  # script left unset
    reuse_db.add(org)
    campaign = CallCampaign(
        org_id=ORG_ID, name="Spring campaign", criteria="Wants a demo this quarter"
    )
    reuse_db.add(campaign)
    await reuse_db.flush()
    target = CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone="+14155551111")
    reuse_db.add(target)
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(target.id, ORG_ID)

    assert OUTBOUND_CALL_PROMPT.strip() in instructions
    assert "Wants a demo this quarter" in instructions


async def test_non_campaign_call_uses_org_script_when_set(reuse_db: AsyncSession) -> None:
    """Confirms the org script DOES work correctly on the non-campaign path
    (single admin call / AI callback / follow-up call) — isolates the gap to
    campaign calls specifically, not the script-reading code in general."""
    org = Org(id=ORG_ID, name="Test Org", script="MY CUSTOM SCRIPT: always be polite.")
    reuse_db.add(org)
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(None, ORG_ID)

    assert "MY CUSTOM SCRIPT" in instructions
    assert OUTBOUND_CALL_PROMPT.strip() not in instructions


async def test_non_campaign_call_falls_back_to_platform_default_with_no_script(
    reuse_db: AsyncSession,
) -> None:
    org = Org(id=ORG_ID, name="Test Org")  # script left unset
    reuse_db.add(org)
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(None, ORG_ID)

    assert OUTBOUND_CALL_PROMPT.strip() in instructions
