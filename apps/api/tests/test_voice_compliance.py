"""Phase 5: recording notice / consent / retention (channels/voice/compliance.py), AI
disclosure, and how they reach a live call.

The policy is the org's to choose; these tests check that it is applied exactly as configured,
that an org that configured nothing keeps today's behaviour, and that one org's policy never
affects another's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.channels.voice import call_control, compliance
from apps.api.channels.voice import realtime_bridge as bridge
from apps.api.channels.voice import webhook as webhook_module
from apps.api.channels.voice.compliance import LEGACY_POLICY, VoiceCompliance, resolve_recording_policy, validate
from apps.api.channels.voice.greeting import effective_brand, greeting_instruction
from apps.api.config import settings
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.db.models import Conversation, Org, User

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000aaaa")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000bbbb")
ADMIN = {"X-Admin-Token": settings.admin_token}


# --- the shape of a policy ---------------------------------------------------------------------------------------------------


def test_a_valid_policy_is_accepted_and_normalised() -> None:
    parsed = validate({
        "recording_mode": "notice",
        "recording_notice": "  This call   may be recorded.  ",
        "regions": {"91": {"recording_mode": "consent"}, "1": {"recording_mode": "off", "recording_notice": "No recording."}},
        "retention_days": 90,
    })
    assert parsed.recording_notice == "This call may be recorded."
    assert parsed.regions["91"].recording_mode == "consent" and parsed.retention_days == 90


@pytest.mark.parametrize(
    "bad",
    [
        {"recording_mode": "sometimes"},
        {"unknown_field": 1},
        {"regions": {"IN": {"recording_mode": "off"}}},
        {"regions": {"12345": {"recording_mode": "off"}}},
        {"regions": {str(i): {} for i in range(31)}},
        {"retention_days": 0},
        {"retention_days": 4000},
        {"recording_notice": "x" * 301},
        {"recording_notice": "bell \x07 char"},
        {"regions": {"91": {"recording_mode": "off", "extra": True}}},
    ],
)
def test_malformed_policies_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate(bad)


# --- which policy applies ----------------------------------------------------------------------------------------------------


def test_nothing_configured_means_todays_behaviour() -> None:
    for unset in (None, {}):
        policy = resolve_recording_policy(unset, "+919876543210")
        assert policy is LEGACY_POLICY
    assert LEGACY_POLICY.mode == "always" and LEGACY_POLICY.records_automatically and LEGACY_POLICY.spoken_notice is None


def test_a_stored_policy_that_no_longer_validates_is_ignored_not_fatal() -> None:
    assert resolve_recording_policy({"recording_mode": "mystery"}, "+91...") is LEGACY_POLICY


@pytest.mark.parametrize(
    ("mode", "records", "notice"),
    [("always", True, None), ("notice", True, compliance.DEFAULT_NOTICE), ("consent", False, None), ("off", False, None)],
)
def test_each_mode_does_what_it_says(mode: str, records: bool, notice: str | None) -> None:
    policy = resolve_recording_policy({"recording_mode": mode}, None)
    assert policy.mode == mode and policy.records_automatically is records and policy.spoken_notice == notice


def test_a_custom_notice_is_used_only_in_notice_mode() -> None:
    assert resolve_recording_policy({"recording_mode": "notice", "recording_notice": "We record."}, None).spoken_notice == "We record."
    assert resolve_recording_policy({"recording_mode": "always", "recording_notice": "We record."}, None).spoken_notice is None


def test_the_longest_matching_calling_code_wins() -> None:
    raw = {
        "recording_mode": "always",
        "regions": {"1": {"recording_mode": "consent"}, "1415": {"recording_mode": "off"}, "91": {"recording_mode": "notice"}},
    }
    assert resolve_recording_policy(raw, "+14155550100").mode == "off"
    assert resolve_recording_policy(raw, "+12125550100").mode == "consent"
    assert resolve_recording_policy(raw, "+919876543210").mode == "notice"
    assert resolve_recording_policy(raw, "0091 98765 43210").mode == "notice"  # dialling-prefix variants
    assert resolve_recording_policy(raw, "+447700900123").mode == "always"  # no region: the org default
    assert resolve_recording_policy(raw, None).mode == "always" and resolve_recording_policy(raw, "unknown").mode == "always"


def test_a_region_can_override_only_part_of_the_policy() -> None:
    raw = {"recording_mode": "notice", "recording_notice": "Base notice.", "regions": {"91": {"recording_mode": "consent"}}}
    india = resolve_recording_policy(raw, "+91987")
    assert india.mode == "consent" and india.notice == "Base notice."
    raw["regions"]["91"] = {"recording_notice": "Regional notice."}
    assert resolve_recording_policy(raw, "+91987") == resolve_recording_policy(raw, "+91987")
    assert resolve_recording_policy(raw, "+91987").notice == "Regional notice." and resolve_recording_policy(raw, "+91987").mode == "notice"


# --- what the caller hears ----------------------------------------------------------------------------------------------------


def test_the_default_greeting_still_identifies_the_ai() -> None:
    text = greeting_instruction(None, "Veerox", True)
    assert "AI assistant" in text and "must make clear that you are an AI assistant" in text


def test_a_recording_notice_is_said_right_after_the_greeting() -> None:
    default = greeting_instruction(None, "Veerox", True, notice="This call may be recorded.")
    assert "Right after the greeting, say once, briefly: 'This call may be recorded.'" in default
    custom = greeting_instruction("Namaste, main Acme ka AI assistant hoon.", "x", True, notice="This call may be recorded.")
    assert "exactly this greeting" in custom and "This call may be recorded." in custom and "straight after it" in custom


def test_without_a_notice_the_instruction_is_unchanged() -> None:
    assert greeting_instruction(None, "WorkAssign AI", True) == greeting_instruction(None, "WorkAssign AI", True, notice=None)
    assert "notice" not in greeting_instruction("Namaste, main Acme ka AI assistant hoon.", "x", True)


def test_the_consent_prompt_forbids_recording_without_a_clear_yes() -> None:
    text = compliance.CONSENT_PROMPT
    assert "must NOT be recorded unless the caller clearly agrees" in text and "granted=false for a no or anything unclear" in text


# --- the brand an org speaks ----------------------------------------------------------------------------------------------------


def test_orgs_that_predate_the_brand_column_keep_the_brand_they_always_had() -> None:
    assert effective_brand(SimpleNamespace(voice_brand=None)) == settings.voice_brand_name
    assert effective_brand(None) == settings.voice_brand_name
    assert effective_brand(SimpleNamespace(voice_brand="Acme Realty")) == "Acme Realty"
    assert effective_brand(SimpleNamespace(voice_brand="")) == settings.voice_brand_name


async def test_a_newly_provisioned_org_speaks_the_new_default_brand(client: AsyncClient, db_session: Any) -> None:
    created = await client.post(
        "/auth/provision-org",
        json={"org_name": "Brand Co", "email": "brand@example.com", "mobile": "+919876500111"},
        headers=ADMIN,
    )
    assert created.status_code == 201
    org = await db_session.get(Org, uuid.UUID(created.json()["org_id"]))
    assert org.voice_brand == settings.voice_new_org_brand == "Veerox"
    assert effective_brand(org) == "Veerox"


# --- admin API ---------------------------------------------------------------------------------------------------------------------


@pytest.fixture
async def orgs(db_session: Any) -> Any:
    db_session.add_all([Org(id=ORG_A, name="A"), Org(id=ORG_B, name="B")])
    await db_session.commit()
    return db_session


async def test_the_default_policy_is_reported_as_the_legacy_behaviour(client: AsyncClient, orgs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_A))
    body = (await client.get("/admin/settings/voice-compliance", headers=ADMIN)).json()
    assert body == {"compliance": None, "is_default": True, "default_recording_mode": "always"}


async def test_a_policy_can_be_set_read_back_and_cleared(client: AsyncClient, orgs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_A))
    put = await client.put(
        "/admin/settings/voice-compliance",
        json={"compliance": {"recording_mode": "consent", "regions": {"91": {"recording_mode": "notice"}}, "retention_days": 30}},
        headers=ADMIN,
    )
    assert put.status_code == 200 and put.json()["is_default"] is False
    org = await orgs.get(Org, ORG_A)
    await orgs.refresh(org)
    assert org.voice_compliance == {"recording_mode": "consent", "regions": {"91": {"recording_mode": "notice"}}, "retention_days": 30}

    cleared = await client.put("/admin/settings/voice-compliance", json={"compliance": None}, headers=ADMIN)
    assert cleared.json()["is_default"] is True
    await orgs.refresh(org)
    assert org.voice_compliance is None


async def test_an_invalid_policy_is_rejected_with_a_readable_reason_and_nothing_is_saved(
    client: AsyncClient, orgs: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "default_org_id", str(ORG_A))
    bad = await client.put("/admin/settings/voice-compliance", json={"compliance": {"recording_mode": "maybe"}}, headers=ADMIN)
    assert bad.status_code == 422 and "recording_mode" in bad.json()["detail"]
    worse = await client.put("/admin/settings/voice-compliance", json={"compliance": {"regions": {"IN": {}}}}, headers=ADMIN)
    assert worse.status_code == 422 and "calling code" in worse.json()["detail"]
    org = await orgs.get(Org, ORG_A)
    await orgs.refresh(org)
    assert org.voice_compliance is None


async def test_one_orgs_policy_is_invisible_to_another(client: AsyncClient, orgs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    org_b = await orgs.get(Org, ORG_B)
    org_b.voice_compliance = {"recording_mode": "off"}
    await orgs.commit()
    monkeypatch.setattr(settings, "default_org_id", str(ORG_A))
    assert (await client.get("/admin/settings/voice-compliance", headers=ADMIN)).json()["is_default"] is True
    await client.put("/admin/settings/voice-compliance", json={"compliance": {"recording_mode": "notice"}}, headers=ADMIN)
    await orgs.refresh(org_b)
    assert org_b.voice_compliance == {"recording_mode": "off"}


# --- the answer webhook starts recording only when the policy says to ---------------------------------------------------------


@pytest.fixture
def recorded(orgs: Any, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []

    class _Ctx:
        async def __aenter__(self) -> Any:
            return orgs

        async def __aexit__(self, *_: Any) -> None:
            return None

    async def start(creds: Any, call_uuid: str, callback: str) -> None:
        started.append(call_uuid)

    monkeypatch.setattr(webhook_module, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(webhook_module, "start_precall_connect", lambda *a, **k: None)
    monkeypatch.setattr(webhook_module, "resolve_plivo_credentials", lambda org: PlivoCredentials("id", "tok"))
    monkeypatch.setattr(webhook_module.voice_plivo, "start_recording", start)
    return started


async def _answer(client: AsyncClient, org: uuid.UUID, caller: str, call_uuid: str = "cu-1") -> None:
    response = await client.post(
        f"/voice/answer?org_id={org}",
        # With ?org_id the call is one the org placed (outbound): the remote party is "To".
        content=f"CallUUID={call_uuid}&From=%2B911111&To={caller.replace('+', '%2B')}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("raw", "recorded_call"),
    [
        (None, True),                                   # nothing configured: the legacy behaviour
        ({"recording_mode": "always"}, True),
        ({"recording_mode": "notice"}, True),
        ({"recording_mode": "consent"}, False),         # only after the caller agrees, on the call
        ({"recording_mode": "off"}, False),
    ],
)
async def test_recording_follows_the_orgs_mode(client: AsyncClient, orgs: Any, recorded: list[str], raw: Any, recorded_call: bool) -> None:
    org = await orgs.get(Org, ORG_A)
    org.voice_compliance = raw
    await orgs.commit()
    await _answer(client, ORG_A, "+919876543210")
    assert (recorded == ["cu-1"]) is recorded_call


async def test_the_callers_region_can_change_the_decision(client: AsyncClient, orgs: Any, recorded: list[str]) -> None:
    org = await orgs.get(Org, ORG_A)
    org.voice_compliance = {"recording_mode": "off", "regions": {"91": {"recording_mode": "notice"}}}
    await orgs.commit()
    await _answer(client, ORG_A, "+14155550100", "us-call")
    await _answer(client, ORG_A, "+919876543210", "in-call")
    assert recorded == ["in-call"]


async def test_another_orgs_policy_does_not_affect_this_calls_recording(client: AsyncClient, orgs: Any, recorded: list[str]) -> None:
    org_b = await orgs.get(Org, ORG_B)
    org_b.voice_compliance = {"recording_mode": "off"}
    await orgs.commit()
    await _answer(client, ORG_A, "+919876543210", "a-call")  # org A configured nothing
    assert recorded == ["a-call"]


# --- the live call's context -----------------------------------------------------------------------------------------------------


@pytest.fixture
def bridge_db(orgs: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    class _Ctx:
        async def __aenter__(self) -> Any:
            return orgs

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Ctx())
    return orgs


async def test_the_call_context_gives_each_org_its_own_brand_and_policy(bridge_db: Any) -> None:
    org_a, org_b = await bridge_db.get(Org, ORG_A), await bridge_db.get(Org, ORG_B)
    org_b.voice_brand = "Acme"
    org_b.voice_compliance = {"recording_mode": "consent", "regions": {"1": {"recording_mode": "always"}}}
    await bridge_db.commit()
    brand_a, policy_a = await bridge._call_context(ORG_A, "+919876543210")
    brand_b, policy_b = await bridge._call_context(ORG_B, "+919876543210")
    _, policy_b_us = await bridge._call_context(ORG_B, "+14155550100")
    assert (brand_a, policy_a.mode) == (settings.voice_brand_name, "always")  # unchanged for the org that set nothing
    assert (brand_b, policy_b.mode, policy_b_us.mode) == ("Acme", "consent", "always")


async def test_a_failed_lookup_means_the_legacy_context_not_a_failed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        async def __aenter__(self) -> Any:
            raise RuntimeError("db down")

        async def __aexit__(self, *_: Any) -> None:
            return None

    monkeypatch.setattr(bridge, "AsyncSessionLocal", lambda: _Boom())
    brand, policy = await bridge._call_context(ORG_A, "+91987")
    assert brand == settings.voice_brand_name and policy is LEGACY_POLICY


async def test_consent_is_asked_only_on_calls_whose_policy_requires_it(bridge_db: Any) -> None:
    org_b = await bridge_db.get(Org, ORG_B)
    org_b.voice_compliance = {"recording_mode": "consent"}
    await bridge_db.commit()
    plain = await bridge._system_instructions(None, ORG_A, remote_number="+919876543210")
    asked = await bridge._system_instructions(None, ORG_B, remote_number="+919876543210")
    assert compliance.CONSENT_PROMPT not in plain and compliance.CONSENT_PROMPT in asked


async def test_the_spoken_brand_in_the_prompt_is_the_orgs_own(bridge_db: Any) -> None:
    org_a, org_b = await bridge_db.get(Org, ORG_A), await bridge_db.get(Org, ORG_B)
    org_b.voice_brand = "Veerox"
    await bridge_db.commit()
    legacy = await bridge._system_instructions(None, ORG_A)
    new = await bridge._system_instructions(None, ORG_B)
    assert f"{settings.voice_brand_name} ka AI assistant" in legacy and "Veerox ka AI assistant" not in legacy
    assert "Veerox ka AI assistant" in new and f"{settings.voice_brand_name} ka AI assistant" not in new


async def test_consent_recording_uses_the_orgs_own_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Any] = []

    async def org(_: Any) -> Any:
        return SimpleNamespace(id=ORG_A)

    async def start(creds: Any, call_id: str, callback: str) -> None:
        started.append((creds, call_id, callback))

    from apps.api.channels.voice import plivo_client, twilio_client

    monkeypatch.setattr(call_control, "_load_org", org)
    monkeypatch.setattr(call_control, "resolve_plivo_credentials", lambda o: PlivoCredentials("pid", "ptok"))
    monkeypatch.setattr(call_control, "resolve_twilio_credentials", lambda o: None)
    monkeypatch.setattr(plivo_client, "start_recording", start)
    monkeypatch.setattr(twilio_client, "start_recording", start)

    class _State:
        org_id, provider, call_uuid = ORG_A, "plivo", "call-1"

    class _Log:
        def warning(self, *a: Any, **k: Any) -> None:
            return None

    assert await call_control.start_call_recording(_State(), _Log()) is True
    assert started[0][0] == PlivoCredentials("pid", "ptok") and started[0][1] == "call-1"
    assert started[0][2].endswith("/voice/recording-callback")
    _State.provider = "twilio"  # no Twilio credentials for this org: nothing is started
    assert await call_control.start_call_recording(_State(), _Log()) is False and len(started) == 1


# --- retention -------------------------------------------------------------------------------------------------------------------------


NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


async def _conversation(db: Any, org: uuid.UUID, user: uuid.UUID, *, age_days: float | None, url: str | None = "https://rec/x.mp3", channel: str = "voice") -> Conversation:
    conversation = Conversation(
        org_id=org, user_id=user, channel=channel, recording_url=url, recording_duration_secs=12.0 if url else None,
        ended_at=None if age_days is None else NOW - timedelta(days=age_days),
    )
    db.add(conversation)
    await db.commit()
    return conversation


async def test_expired_recording_references_are_cleared_per_org_policy(orgs: Any) -> None:
    orgs.add_all([User(id=USER_A, org_id=ORG_A, phone="+91000000001"), User(id=USER_B, org_id=ORG_B, phone="+91000000001")])
    org_a, org_b = await orgs.get(Org, ORG_A), await orgs.get(Org, ORG_B)
    org_a.voice_compliance = {"retention_days": 30}   # keeps 30 days
    # org B configured nothing: it keeps everything.
    await orgs.commit()
    old_a = await _conversation(orgs, ORG_A, USER_A, age_days=45)
    new_a = await _conversation(orgs, ORG_A, USER_A, age_days=10)
    ongoing = await _conversation(orgs, ORG_A, USER_A, age_days=None)
    text_a = await _conversation(orgs, ORG_A, USER_A, age_days=45, channel="whatsapp")
    old_b = await _conversation(orgs, ORG_B, USER_B, age_days=400)

    cleared = await compliance.purge_expired_recordings(orgs, now=NOW)

    assert cleared == 1
    for conversation in (old_a, new_a, ongoing, text_a, old_b):
        await orgs.refresh(conversation)
    assert old_a.recording_url is None and old_a.recording_duration_secs is None
    assert new_a.recording_url and ongoing.recording_url and text_a.recording_url
    assert old_b.recording_url  # another org's retention (here: none) is never applied


async def test_purging_twice_changes_nothing_more(orgs: Any) -> None:
    orgs.add(User(id=USER_A, org_id=ORG_A, phone="+91000000001"))
    (await orgs.get(Org, ORG_A)).voice_compliance = {"retention_days": 7}
    await orgs.commit()
    await _conversation(orgs, ORG_A, USER_A, age_days=20)
    assert await compliance.purge_expired_recordings(orgs, now=NOW) == 1
    assert await compliance.purge_expired_recordings(orgs, now=NOW) == 0


async def test_the_retention_worker_is_off_by_default_and_a_bad_policy_clears_nothing(orgs: Any) -> None:
    assert settings.voice_retention_worker_enabled is False
    orgs.add(User(id=USER_A, org_id=ORG_A, phone="+91000000001"))
    (await orgs.get(Org, ORG_A)).voice_compliance = {"retention_days": "soon"}  # invalid -> legacy -> keep everything
    await orgs.commit()
    old = await _conversation(orgs, ORG_A, USER_A, age_days=999)
    assert await compliance.purge_expired_recordings(orgs, now=NOW) == 0
    await orgs.refresh(old)
    assert old.recording_url


async def test_the_callers_consent_answer_is_recorded_on_that_conversation_only(orgs: Any) -> None:
    orgs.add_all([User(id=USER_A, org_id=ORG_A, phone="+91000000001"), User(id=USER_B, org_id=ORG_B, phone="+91000000001")])
    await orgs.commit()
    mine = await _conversation(orgs, ORG_A, USER_A, age_days=None, url=None)
    theirs = await _conversation(orgs, ORG_B, USER_B, age_days=None, url=None)
    await compliance.record_consent_outcome(orgs, mine.id, ORG_B, True)  # wrong org: ignored
    await orgs.refresh(mine)
    assert not mine.tags
    await compliance.record_consent_outcome(orgs, mine.id, ORG_A, False)
    await compliance.record_consent_outcome(orgs, mine.id, ORG_A, True)  # the latest answer replaces the earlier one
    await orgs.refresh(mine)
    await orgs.refresh(theirs)
    assert mine.tags == ["recording:consented"] and not theirs.tags
