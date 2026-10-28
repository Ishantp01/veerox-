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

Voice calling now resolves its base script from the `scripts` table (see
db/models/script.py) rather than the legacy `Org.script` column, which is
WhatsApp-only these days — a campaign's own pinned `script_id` wins, else
the org's `is_default=True` script, else the platform default.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import realtime_bridge as bridge_module
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT
from apps.api.db.models import CallCampaign, CampaignTarget, Org, Script

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


async def test_campaign_call_uses_org_default_script_as_base_plus_criteria(
    reuse_db: AsyncSession,
) -> None:
    """The exact bug report, now fixed: an org has a default script
    configured — a campaign call for that same org must use it as the base,
    with the campaign's own `criteria` layered on top as an addendum, not
    swapped out for a generic unrelated template."""
    org = Org(id=ORG_ID, name="Test Org")
    reuse_db.add(org)
    reuse_db.add(
        Script(
            org_id=ORG_ID,
            name="Default",
            content="MY CUSTOM SCRIPT: always mention our 20% launch discount.",
            is_default=True,
        )
    )
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


async def test_a_voice_call_never_uses_the_whatsapp_default_script(reuse_db: AsyncSession) -> None:
    """Each channel has its own default; the WhatsApp one must not be picked up by a call, whichever row the
    database returns first."""
    reuse_db.add(Org(id=ORG_ID, name="Test Org"))
    reuse_db.add(Script(org_id=ORG_ID, name="WA", content="WHATSAPP DEFAULT", channel="whatsapp", is_default=True))
    reuse_db.add(Script(org_id=ORG_ID, name="Voice", content="VOICE DEFAULT", channel="voice", is_default=True))
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(None, ORG_ID)

    assert "VOICE DEFAULT" in instructions
    assert "WHATSAPP DEFAULT" not in instructions


async def test_the_script_a_call_loaded_is_logged_without_its_text(reuse_db: AsyncSession) -> None:
    from structlog.testing import capture_logs

    reuse_db.add(Org(id=ORG_ID, name="Test Org"))
    reuse_db.add(Script(org_id=ORG_ID, name="Main voice", content="SECRET WORDING", is_default=True))
    await reuse_db.commit()

    with capture_logs() as logs:
        await bridge_module._system_instructions(None, ORG_ID)
        await bridge_module._system_instructions(None, None)

    entries = [e for e in logs if e["event"] == "voice_script_resolved"]
    assert [(e["source"], e["script_name"]) for e in entries] == [("org_default", "Main voice"), ("platform_default", None)]
    assert "SECRET WORDING" not in str(entries)


async def test_a_whatsapp_only_org_falls_back_to_the_platform_script_for_calls(reuse_db: AsyncSession) -> None:
    reuse_db.add(Org(id=ORG_ID, name="Test Org"))
    reuse_db.add(Script(org_id=ORG_ID, name="WA", content="WHATSAPP DEFAULT", channel="whatsapp", is_default=True))
    await reuse_db.commit()

    assert "WHATSAPP DEFAULT" not in await bridge_module._system_instructions(None, ORG_ID)


async def test_campaign_call_uses_its_own_pinned_script_over_org_default(
    reuse_db: AsyncSession,
) -> None:
    """A campaign's own script_id wins over the org's default script — a
    campaign author who explicitly picked a script must see it followed,
    not silently overridden by whatever the org's default happens to be."""
    org = Org(id=ORG_ID, name="Test Org")
    reuse_db.add(org)
    reuse_db.add(Script(org_id=ORG_ID, name="Default", content="ORG DEFAULT SCRIPT", is_default=True))
    pinned = Script(org_id=ORG_ID, name="Spring promo", content="PINNED CAMPAIGN SCRIPT")
    reuse_db.add(pinned)
    await reuse_db.flush()
    campaign = CallCampaign(
        org_id=ORG_ID, name="Spring campaign", criteria="Wants a demo this quarter", script_id=pinned.id
    )
    reuse_db.add(campaign)
    await reuse_db.flush()
    target = CampaignTarget(campaign_id=campaign.id, org_id=ORG_ID, phone="+14155551111")
    reuse_db.add(target)
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(target.id, ORG_ID)

    assert "PINNED CAMPAIGN SCRIPT" in instructions
    assert "ORG DEFAULT SCRIPT" not in instructions


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


async def test_non_campaign_call_uses_org_default_script_when_set(reuse_db: AsyncSession) -> None:
    """Confirms the org's default script DOES work correctly on the
    non-campaign path (single admin call / AI callback / follow-up call) —
    isolates the gap to campaign calls specifically, not the script-reading
    code in general."""
    org = Org(id=ORG_ID, name="Test Org")
    reuse_db.add(org)
    reuse_db.add(
        Script(org_id=ORG_ID, name="Default", content="MY CUSTOM SCRIPT: always be polite.", is_default=True)
    )
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


async def test_voice_instructions_use_one_brand_and_disclose_the_ai(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt's example greeting used to say "Veerox" while the call's
    opening instruction said "WorkAssign AI". Both now come from
    settings.voice_brand_name, and the example tells callers it's an AI."""
    from apps.api.config import settings

    monkeypatch.setattr(settings, "voice_brand_name", "Acme Realty")
    monkeypatch.setattr(settings, "voice_greeting_ai_disclosure", True)
    reuse_db.add(Org(id=ORG_ID, name="Test Org"))
    await reuse_db.commit()

    instructions = await bridge_module._system_instructions(None, ORG_ID)

    assert "Acme Realty ka AI assistant" in instructions
    assert "Veerox se baat kar rahe hain" not in instructions


async def test_org_greeting_lookup_defaults_to_none_and_survives_errors(
    reuse_db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    reuse_db.add(Org(id=ORG_ID, name="Test Org"))
    await reuse_db.commit()
    assert await bridge_module._load_org_greeting(ORG_ID) is None  # existing orgs: default greeting

    org = await reuse_db.get(Org, ORG_ID)
    org.voice_greeting = "Namaste, main Acme ka AI assistant hoon."
    await reuse_db.commit()
    assert await bridge_module._load_org_greeting(ORG_ID) == "Namaste, main Acme ka AI assistant hoon."

    class _Boom:
        async def __aenter__(self) -> None:
            raise RuntimeError("db down")

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge_module, "AsyncSessionLocal", lambda: _Boom())
    assert await bridge_module._load_org_greeting(ORG_ID) is None  # a lookup failure never fails the call
