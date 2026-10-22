"""Phase 8A: pilot-mode guard rails and the remaining safety gaps. Offline: no network, no real provider.

Pilot mode is ENVIRONMENT=pilot. Every test that turns it on does so with monkeypatch, so nothing here can leak
into another test or into a real environment, and the first group proves it is inert everywhere else.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from apps.api import main as main_module
from apps.api import pilot
from apps.api.channels.email import brevo_client
from apps.api.channels.voice import adapter, failover, plivo_client
from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core import tools
from apps.api.core.conversation import dialogue, voice_decision
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.core.tools import request_callback
from apps.api.db.models import Appointment, Org, User
from apps.api.tests.test_tools import ORG_ID, _seed_org, fake_redis  # noqa: F401
from apps.api.tests.test_voice_decision import _state, _task_returning

CREDS = PlivoCredentials(auth_id="AID", auth_token="tok")


def _pilot(monkeypatch: pytest.MonkeyPatch, *, numbers: str = "", emails: str = "", workers: bool = False) -> None:
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "pilot_allowed_numbers", numbers)
    monkeypatch.setattr(settings, "pilot_allowed_emails", emails)
    monkeypatch.setattr(settings, "pilot_workers_enabled", workers)
    # These tests are about the allowlist; webhook signatures (enforced by default in pilot mode) have their own file.
    monkeypatch.setattr(settings, "plivo_signature_mode", "off")


# --- inert outside pilot mode ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prod", "production", "staging", ""])
def test_outside_pilot_mode_nothing_is_restricted(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setattr(settings, "environment", env)
    monkeypatch.setattr(settings, "pilot_allowed_numbers", "")
    assert pilot.pilot_mode() is False and pilot.workers_enabled() is True
    pilot.check_number_allowed("+919999999999", what="call")  # no error
    pilot.check_email_allowed("anyone@example.com")


def test_the_defaults_leave_every_pilot_setting_off() -> None:
    assert settings.pilot_allowed_numbers == "" and settings.pilot_allowed_emails == ""
    assert settings.pilot_workers_enabled is False and settings.pilot_database_name == ""


# --- allowlists fail closed -----------------------------------------------------------------------------------------------------------------


def test_an_empty_allowlist_blocks_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_number_allowed("+919876543210", what="call")
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_email_allowed("a@b.com")


@pytest.mark.parametrize("to", ["+919876543210", "9876543210", "+91 98765 43210", "09876543210"])
def test_an_allowlisted_number_passes_in_any_format(monkeypatch: pytest.MonkeyPatch, to: str) -> None:
    _pilot(monkeypatch, numbers="+91 98765-43210, +14155550100")
    pilot.check_number_allowed(to, what="call")


def test_a_number_not_on_the_list_is_blocked_and_the_log_masks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    with pytest.raises(pilot.PilotBlocked, match="PILOT_ALLOWED_NUMBERS") as err:
        pilot.check_number_allowed("+919111111111", what="call")
    assert "9111111111" not in str(err.value)


def test_email_matching_is_exact_and_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, emails="Me@Example.com")
    pilot.check_email_allowed("me@example.com")
    with pytest.raises(pilot.PilotBlocked):
        pilot.check_email_allowed("me@example.com.evil.io")


def test_a_refusal_is_a_provider_error_to_every_existing_caller() -> None:
    assert issubclass(pilot.PilotBlocked, httpx.HTTPError)


# --- the guards sit on the real send paths, before any request is made --------------------------------------------------------------


async def test_a_blocked_outbound_call_never_reaches_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    reached: list[str] = []

    async def must_not_run(*a: Any, **k: Any) -> dict[str, Any]:
        reached.append("provider")
        return {}

    monkeypatch.setattr(plivo_client, "initiate_call", must_not_run)
    with pytest.raises(pilot.PilotBlocked):
        await failover.initiate_call(CREDS, None, "+919111111111", "https://x/answer", plivo_from_number="+911234567890")
    assert reached == []
    result, provider = await failover.initiate_call(CREDS, None, "+919876543210", "https://x/answer", plivo_from_number="+911234567890")
    assert reached == ["provider"] and provider == "plivo"  # an allowlisted number still works


async def test_a_blocked_sms_and_whatsapp_never_make_an_http_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={})

    monkeypatch.setattr(wa_client, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(brevo_client, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)), raising=False)
    with pytest.raises(pilot.PilotBlocked):
        await wa_client.send_text("tok", "+919111111111", "hi")
    with pytest.raises(pilot.PilotBlocked):
        await wa_client.send_template("tok", "+919111111111", "t")
    with pytest.raises(pilot.PilotBlocked):
        await wa_client.send_media("tok", "+919111111111", "image", "https://x/y.png")
    with pytest.raises(pilot.PilotBlocked):
        await failover.send_sms(CREDS, None, "+919111111111", "hi", plivo_from_number="+911234567890")
    with pytest.raises(pilot.PilotBlocked):
        await brevo_client.send_email("someone@real.com", "s", "<p>x</p>")
    assert calls == []


# --- background workers ------------------------------------------------------------------------------------------------------------------


async def _run_lifespan(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    started: list[str] = []

    def make(name: str) -> Any:
        async def worker(*a: Any, **k: Any) -> None:
            started.append(name)
            await asyncio.sleep(3600)

        return worker

    for name in ("run_campaign_dialer", "run_whatsapp_dispatcher", "run_follow_up_dispatcher", "run_license_expiry_worker",
                 "run_usage_daily_aggregator", "run_usage_monthly_aggregator", "run_aws_cost_importer",
                 "run_late_event_adjustment_sweep", "run_voice_retention_worker"):
        monkeypatch.setattr(main_module, name, make(name))

    async def no_registration(db: Any) -> None:
        started.append("plivo_registration")

    async def no_seed() -> None:
        return None

    monkeypatch.setattr(main_module, "register_all_org_plivo_numbers", no_registration)
    monkeypatch.setattr(main_module, "ensure_env_seed_data_on_startup", no_seed)
    async with main_module.lifespan(main_module.FastAPI()):
        await asyncio.sleep(0.05)
    return started


async def test_every_worker_starts_outside_pilot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    assert len(await _run_lifespan(monkeypatch)) == 10


async def test_pilot_mode_starts_no_worker_and_no_plivo_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    assert await _run_lifespan(monkeypatch) == []


async def test_workers_can_be_turned_back_on_for_a_pilot_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, workers=True)
    assert len(await _run_lifespan(monkeypatch)) == 10


# --- guards that hold even when the decision plan is late --------------------------------------------------------------------------------


async def _late_plan_turn(monkeypatch: pytest.MonkeyPatch, text: str, **state_fields: Any) -> adapter.CallState:
    voice_decision.set_cached_voice_mode(ORG_ID, "live")
    state = _state(org_id=ORG_ID, voice_decision_text=text, **state_fields)
    state.pending_user_transcript = text
    state.voice_decision_started = 0.0  # the budget is long gone
    state.voice_decision_task = _task_returning(None, delay=5)
    monkeypatch.setattr(settings, "conversation_voice_decision_budget_seconds", 0.01)
    await adapter._voice_decision_response(state)
    return state


async def test_a_late_plan_still_holds_booking_and_closing_for_an_enhanced_org(monkeypatch: pytest.MonkeyPatch) -> None:
    state = await _late_plan_turn(monkeypatch, "Mujhe meeting nahi chahiye", dialogue_enhanced=True)
    assert set(state.tool_holds) == {"book_appointment", "mark_not_interested"} and state.meeting_declined is True


async def test_a_refusal_is_remembered_for_later_turns_whose_plan_is_also_late(monkeypatch: pytest.MonkeyPatch) -> None:
    state = await _late_plan_turn(monkeypatch, "Aur kuch batao", dialogue_enhanced=True, meeting_declined=True)
    assert "book_appointment" in state.tool_holds  # still no booking against a stated refusal


async def test_a_late_plan_changes_nothing_for_an_org_not_known_to_be_enhanced(monkeypatch: pytest.MonkeyPatch) -> None:
    state = await _late_plan_turn(monkeypatch, "Mujhe meeting nahi chahiye")
    assert state.tool_holds == {}


async def test_a_correction_with_a_late_plan_holds_the_booking(monkeypatch: pytest.MonkeyPatch) -> None:
    state = await _late_plan_turn(monkeypatch, "Nahi, Tuesday.", dialogue_enhanced=True)
    assert "book_appointment" in state.tool_holds and "mark_not_interested" not in state.tool_holds


def test_holds_from_words_for_a_plain_message_are_empty() -> None:
    assert dialogue.text_tool_holds("Kal 5 baje theek hai", meeting_declined=False, today=datetime.now(UTC).date()) == {}


# --- callbacks: past times, timezones, duplicates, tenants ---------------------------------------------------------------------------------


async def _user(db: Any) -> User:
    await _seed_org(db)
    user = User(org_id=ORG_ID, phone="+910000000055", name="Meera")
    db.add(user)
    await db.commit()
    return user


def _when(delta: timedelta) -> tuple[str, str]:
    moment = datetime.now(UTC) + delta
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M")


async def test_a_callback_in_the_past_is_refused_with_guidance_for_the_model(db_session: Any) -> None:
    user = await _user(db_session)
    date, time = _when(timedelta(hours=-3))
    result = await request_callback(db_session, date=date, time=time, timezone="UTC", user_id=user.id, org_id=ORG_ID)
    assert result["status"] == "error" and result["reason"] == "date_in_past" and "Ask the caller" in result["note"]
    assert (await db_session.execute(select(Appointment))).scalars().all() == []


async def test_a_callback_for_right_now_is_not_rejected_for_a_slightly_stale_clock(db_session: Any) -> None:
    user = await _user(db_session)
    date, time = _when(timedelta(minutes=-2))
    result = await request_callback(db_session, date=date, time=time, timezone="UTC", user_id=user.id, org_id=ORG_ID)
    assert result["status"] == "ok"


async def test_the_time_is_read_in_the_stated_timezone_not_the_servers(db_session: Any) -> None:
    user = await _user(db_session)
    date, time = _when(timedelta(hours=2))
    # The same wall-clock time two hours from now in UTC is already past in a zone far ahead of UTC.
    ahead = await request_callback(db_session, date=date, time=time, timezone="Pacific/Kiritimati", user_id=user.id, org_id=ORG_ID)
    assert ahead["reason"] == "date_in_past"
    behind = await request_callback(db_session, date=date, time=time, timezone="Pacific/Pago_Pago", user_id=user.id, org_id=ORG_ID)
    assert behind["status"] == "ok"


@pytest.mark.parametrize("zone", ["UTC+5", "Mars/Olympus", "", "IST"])
async def test_a_bad_timezone_is_refused_not_guessed(db_session: Any, zone: str) -> None:
    user = await _user(db_session)
    date, time = _when(timedelta(hours=2))
    result = await request_callback(db_session, date=date, time=time, timezone=zone or "nowhere", user_id=user.id, org_id=ORG_ID)
    assert result == {"status": "error", "reason": "invalid_timezone"}


@pytest.mark.parametrize(("date", "time"), [("tomorrow", "5pm"), ("2026-13-45", "25:00"), ("", "")])
async def test_a_garbled_date_or_time_is_refused(db_session: Any, date: str, time: str) -> None:
    user = await _user(db_session)
    result = await request_callback(db_session, date=date, time=time, user_id=user.id, org_id=ORG_ID)
    assert result == {"status": "error", "reason": "invalid_date_or_time"}


async def test_the_same_callback_is_one_row_but_another_orgs_is_separate(db_session: Any) -> None:
    user = await _user(db_session)
    other_org = uuid.UUID("00000000-0000-0000-0000-0000000000f8")
    db_session.add(Org(id=other_org, name="Other"))
    other_user = User(org_id=other_org, phone="+910000000056", name="Other")
    db_session.add(other_user)
    await db_session.commit()
    date, time = _when(timedelta(hours=4))
    args = dict(date=date, time=time, timezone="UTC")
    first = await request_callback(db_session, user_id=user.id, org_id=ORG_ID, **args)
    again = await request_callback(db_session, user_id=user.id, org_id=ORG_ID, **args)
    elsewhere = await request_callback(db_session, user_id=other_user.id, org_id=other_org, **args)
    assert again.get("duplicate") is True and again["appointment_id"] == first["appointment_id"]
    assert elsewhere.get("duplicate") is None and elsewhere["appointment_id"] != first["appointment_id"]
    rows = (await db_session.execute(select(Appointment).where(Appointment.callback_at.is_not(None)))).scalars().all()
    assert sorted(str(r.org_id) for r in rows) == sorted([str(ORG_ID), str(other_org)])


async def test_a_failed_tool_is_reported_as_failed_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> dict[str, Any]:
        raise RuntimeError("db down")

    monkeypatch.setitem(tools.DISPATCH_TABLE, "request_callback", boom)
    result = await adapter._dispatch_realtime_tool("request_callback", "{}", uuid.uuid4(), ORG_ID)
    assert result["status"] == "error" and result["reason"].startswith("tool_execution_failed")


# --- pre-flight checks and the example file ---------------------------------------------------------------------------------------------------------

from pathlib import Path

from apps.api import config as config_module
from apps.api.pilot_checks import check_pilot_env, parse_env

ROOT = Path(__file__).resolve().parents[3]
MAIN = parse_env(
    "DATABASE_URL=postgresql+asyncpg://u:p@main-db.example.net/neondb\n"
    "REDIS_URL=rediss://default:pw@main-redis.example.net:6379\n"
    "PUBLIC_BASE_URL=https://main.ngrok-free.dev\nOPENAI_API_KEY=sk-main\nMETA_ACCESS_TOKEN=tok-main\n"
)
GOOD = {
    "ENVIRONMENT": "pilot", "PILOT_DATABASE_NAME": "veerox_pilot",
    "DATABASE_URL": "postgresql+asyncpg://pu:pp@pilot-db.example.net/veerox_pilot",
    "REDIS_URL": "rediss://default:pw@pilot-redis.example.net:6379", "PUBLIC_BASE_URL": "https://pilot.ngrok-free.dev",
    "PILOT_ALLOWED_NUMBERS": "+919876543210", "LOG_TRANSCRIPTS": "false", "PLIVO_STARTUP_REGISTRATION": "false",
}


def _fails(env: dict[str, str]) -> set[str]:
    return {r.check for r in check_pilot_env(env, MAIN) if r.status == "FAIL"}


def test_a_correct_pilot_file_has_no_failures() -> None:
    assert _fails(GOOD) == set()


def test_the_env_parser_handles_quotes_comments_and_export() -> None:
    env = parse_env('# c\nexport A="x y"\nB=\'z\'\n  C = plain \nbad line\n#D=1\n')
    assert env == {"A": "x y", "B": "z", "C": "plain"}


@pytest.mark.parametrize(
    ("override", "expected_failure"),
    [
        ({"DATABASE_URL": MAIN["DATABASE_URL"]}, "database differs from main .env"),
        ({"DATABASE_URL": "postgresql+asyncpg://u:p@main-db.example.net/neondb2", "PILOT_DATABASE_NAME": "neondb2"}, "database name looks disposable"),
        ({"PILOT_DATABASE_NAME": ""}, "database is the declared pilot database"),
        ({"PILOT_DATABASE_NAME": "other_pilot"}, "database is the declared pilot database"),
        ({"REDIS_URL": MAIN["REDIS_URL"]}, "redis instance differs from main .env"),
        ({"REDIS_URL": "rediss://default:pw@main-redis.example.net:6380"}, "redis host differs from main .env"),
        ({"DATABASE_URL": "postgresql+asyncpg://u:p@veerox-db.abc.rds.amazonaws.com/veerox_pilot"}, "no known production host"),
        ({"PUBLIC_BASE_URL": "https://app.workassignai.com"}, "no known production host"),
        ({"ENVIRONMENT": "dev"}, "ENVIRONMENT is pilot"),
        ({"PILOT_WORKERS_ENABLED": "true"}, "background workers stay off"),
        ({"PLIVO_STARTUP_REGISTRATION": "true"}, "no startup Plivo registration"),
        ({"LOG_TRANSCRIPTS": "true"}, "transcripts not logged"),
    ],
)
def test_each_unsafe_difference_is_caught(override: dict[str, str], expected_failure: str) -> None:
    assert expected_failure in _fails({**GOOD, **override})


def test_the_same_tunnel_is_a_warning_not_a_failure() -> None:
    results = check_pilot_env({**GOOD, "PUBLIC_BASE_URL": MAIN["PUBLIC_BASE_URL"]}, MAIN)
    assert [r.status for r in results if r.check == "tunnel differs from main .env"] == ["WARN"]


def test_a_neutral_database_name_is_only_acceptable_on_a_different_server() -> None:
    other_server = {**GOOD, "DATABASE_URL": "postgresql+asyncpg://u:p@new-project.example.net/neondb", "PILOT_DATABASE_NAME": "neondb"}
    assert "database name looks disposable" not in _fails(other_server)
    assert [r.status for r in check_pilot_env(other_server, MAIN) if r.check == "database name looks disposable"] == ["WARN"]
    same_server = {**GOOD, "DATABASE_URL": "postgresql+asyncpg://u:p@main-db.example.net/neondb2", "PILOT_DATABASE_NAME": "neondb2"}
    assert "database name looks disposable" in _fails(same_server)


def test_copied_credentials_and_risky_keys_warn_without_printing_them() -> None:
    results = check_pilot_env({**GOOD, "OPENAI_API_KEY": "sk-main", "META_ACCESS_TOKEN": "tok-main", "SENTRY_DSN": "https://s"}, MAIN)
    warned = {r.check: r.detail for r in results if r.status == "WARN"}
    assert "OPENAI_API_KEY" in warned["no credential copied from main .env"] and "META_ACCESS_TOKEN" in warned["no credential copied from main .env"]
    assert "SENTRY_DSN" in warned["billing/monitoring keys left out"]
    assert "sk-main" not in " ".join(r.detail for r in results) and "tok-main" not in " ".join(r.detail for r in results)


def test_experimental_voice_flags_in_the_pilot_file_are_flagged() -> None:
    warned = [r for r in check_pilot_env({**GOOD, "VOICE_SAFE_CANCEL": "true"}, MAIN) if r.status == "WARN"]
    assert any("VOICE_SAFE_CANCEL" in r.detail for r in warned)


def test_the_example_file_is_complete_safe_and_uses_real_setting_names() -> None:
    text = (ROOT / ".env.pilot.example").read_text(encoding="utf8")
    env = parse_env(text)
    assert env["ENVIRONMENT"] == "pilot" and env["LOG_TRANSCRIPTS"] == "false" and env["PILOT_WORKERS_ENABLED"] == "false"
    assert env["PLIVO_STARTUP_REGISTRATION"] == "false" and env["REDIS_URL"].startswith("rediss://")
    for name in ("META_ACCESS_TOKEN", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "BREVO_API_KEY", "SENTRY_DSN", "META_APP_SECRET"):
        assert name not in env, name  # absent on purpose: no WhatsApp, payments, e-mail or production monitoring
    assert all(env[k] == "false" for k in env if k.startswith("VOICE_") and k not in ("VOICE_LATENCY_TRACKING", "VOICE_TTS_PROVIDER", "VOICE_GREETING_AI_DISCLOSURE") and not k.startswith("VOICE_OUTBOUND_"))  # numeric limits are not behaviour flags
    assert env["VOICE_GREETING_AI_DISCLOSURE"] == "true"  # the AI disclosure is stated, not left to a default
    assert not any("#" in v for v in env.values())  # no inline comments that a dotenv reader could mis-parse
    known = set(config_module.Settings.model_fields)
    unknown = [k for k in env if k.lower() not in known]
    assert unknown == [], unknown  # every name in the example is a real setting
    assert not [r for r in check_pilot_env({**env, "DATABASE_URL": GOOD["DATABASE_URL"], "REDIS_URL": GOOD["REDIS_URL"], "PUBLIC_BASE_URL": GOOD["PUBLIC_BASE_URL"]}, MAIN) if r.status == "FAIL"]


def test_the_real_gitignore_covers_the_filled_in_pilot_file() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf8").splitlines()
    assert ".env.pilot" in lines and ".env.pilot.example" not in lines


def test_the_env_file_can_be_redirected_so_the_real_dotenv_is_not_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Test the default independently of the safe env-file override supplied
    # by offline validation. Resolving a path does not read the real dotenv.
    monkeypatch.delenv("VEEROX_ENV_FILE", raising=False)
    default = config_module.resolve_env_file()
    assert default.name == ".env"
    monkeypatch.setenv("VEEROX_ENV_FILE", str(tmp_path / "pilot.env"))
    assert config_module.resolve_env_file() == (tmp_path / "pilot.env").resolve()
    monkeypatch.delenv("VEEROX_ENV_FILE")
    assert config_module.resolve_env_file() == default


# --- running the pilot on the same data stores, only by the owner's explicit say-so ---------------------------------------------------------------------

from apps.api.pilot_checks import ACK_NAME, ACK_VALUE

SHARED = {**GOOD, "DATABASE_URL": MAIN["DATABASE_URL"], "PILOT_DATABASE_NAME": "neondb", "REDIS_URL": MAIN["REDIS_URL"], "PUBLIC_BASE_URL": MAIN["PUBLIC_BASE_URL"]}


def test_sharing_the_main_data_stores_fails_until_the_owner_acknowledges_it() -> None:
    assert {"database differs from main .env", "redis instance differs from main .env", "database name looks disposable"} <= _fails(SHARED)


def test_the_acknowledgement_turns_those_failures_into_loud_warnings_and_nothing_else() -> None:
    results = check_pilot_env({**SHARED, ACK_NAME: ACK_VALUE}, MAIN)
    assert [r.check for r in results if r.status == "FAIL"] == []
    warned = [r for r in results if r.status == "WARN"]
    assert any(r.check == "pilot shares the main .env's data stores" for r in warned)
    assert sum("ACKNOWLEDGED" in r.detail for r in warned) >= 5


def test_a_misspelled_acknowledgement_is_an_error_not_a_pass() -> None:
    assert "shared-data acknowledgement is exact" in _fails({**SHARED, ACK_NAME: "yes"})


@pytest.mark.parametrize(
    ("override", "still_fails"),
    [
        ({"DATABASE_URL": "postgresql+asyncpg://u:p@veerox-db.abc.rds.amazonaws.com/neondb"}, "no known production host"),
        ({"PILOT_WORKERS_ENABLED": "true"}, "background workers stay off"),
        ({"PLIVO_STARTUP_REGISTRATION": "true"}, "no startup Plivo registration"),
        ({"LOG_TRANSCRIPTS": "true"}, "transcripts not logged"),
        ({"ENVIRONMENT": "dev"}, "ENVIRONMENT is pilot"),
        ({"PILOT_DATABASE_NAME": "wrongname"}, "database is the declared pilot database"),
    ],
)
def test_the_acknowledgement_never_relaxes_the_other_safeguards(override: dict[str, str], still_fails: str) -> None:
    assert still_fails in {r.check for r in check_pilot_env({**SHARED, ACK_NAME: ACK_VALUE, **override}, MAIN) if r.status == "FAIL"}


def test_the_local_pilot_file_keeps_payments_and_monitoring_off_and_gates_messaging() -> None:
    path = ROOT / ".env.pilot"
    if not path.exists():
        pytest.skip("no local .env.pilot")
    env = parse_env(path.read_text(encoding="utf8"))
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "SENTRY_DSN"):
        assert name not in env, name  # payments and production monitoring stay out of a pilot
    assert env["ENVIRONMENT"] == "pilot" and env["PILOT_WORKERS_ENABLED"] == "false" and env["LOG_TRANSCRIPTS"] == "false"
    assert ACK_NAME not in env  # only the owner turns this on
    if "META_ACCESS_TOKEN" in env or "BREVO_API_KEY" in env:  # the owner chose to enable messaging: the allowlists must exist
        assert env.get("PILOT_ALLOWED_NUMBERS", "").strip() and "PILOT_ALLOWED_EMAILS" in env


# --- inbound and outbound answers in pilot mode ---------------------------------------------------------------------------------------------------

from apps.api.channels.voice import webhook as voice_webhook
from apps.api.tests.test_admin_endpoints import client  # noqa: F401,F811


async def test_a_stranger_calling_in_pilot_mode_is_hung_up_on_before_anything_connects(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    started: list[str] = []
    monkeypatch.setattr(voice_webhook, "start_precall_connect", lambda *a, **k: started.append("openai"))
    response = await client.post("/voice/answer", data={"CallUUID": "u1", "From": "+919111111111", "To": "+911234567890"})
    assert response.status_code == 200 and "<Hangup/>" in response.text and "<Stream" not in response.text
    assert started == []  # no OpenAI session was warmed up for the stranger


async def test_an_outbound_answer_to_a_non_allowlisted_number_is_also_refused(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    response = await client.post(f"/voice/answer?org_id={ORG_ID}", data={"CallUUID": "u2", "From": "+911234567890", "To": "+919222222222"})
    assert "<Hangup/>" in response.text and "<Stream" not in response.text


# --- the stream socket, provider configuration changes and recording default (second pilot pass) ----------------------------------------------------

from apps.api.channels.voice import compliance, realtime_bridge
from apps.api.channels.whatsapp import webhook_registration


class _FakeStreamSocket:
    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = params
        self.accepted = False
        self.closed_with: int | None = None

    async def accept(self, *a: Any, **k: Any) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    async def receive_text(self) -> str:
        raise RuntimeError("the stream must not read from a rejected caller")


async def test_the_open_stream_socket_refuses_a_caller_who_is_not_allowlisted_in_pilot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    warmed: list[str] = []
    monkeypatch.setattr(realtime_bridge, "_connect_to_openai", lambda *a, **k: warmed.append("openai"))
    ws = _FakeStreamSocket({"from": "+919111111111", "call_uuid": "u1", "provider": "plivo"})
    await realtime_bridge.voice_stream(ws)  # type: ignore[arg-type]
    assert ws.closed_with == 1008 and warmed == []  # closed before any AI session is opened

    anonymous = _FakeStreamSocket({"call_uuid": "u2"})
    await realtime_bridge.voice_stream(anonymous)  # type: ignore[arg-type]
    assert anonymous.closed_with == 1008  # no caller id at all is refused too


async def _register_with(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(200, json={"objects": [], "app_id": "a1", "api_id": "x"})

    monkeypatch.setattr(plivo_client, "_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await plivo_client.register_inbound_answer_url(CREDS, "+911234567890")
    return calls


async def test_pilot_mode_does_not_repoint_a_plivo_number_unless_explicitly_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    assert await _register_with(monkeypatch) == []  # skipped quietly: saving a setting cannot move a real number
    monkeypatch.setattr(settings, "pilot_allow_provider_config_changes", True)
    assert await _register_with(monkeypatch) != []  # the approved step is possible


async def test_outside_pilot_mode_the_plivo_registration_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    assert await _register_with(monkeypatch) != []


async def test_pilot_mode_does_not_touch_the_meta_webhook(monkeypatch: pytest.MonkeyPatch, db_session: Any) -> None:
    _pilot(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(webhook_registration, "_http", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(str(r.url)) or httpx.Response(200, json={}))))
    await webhook_registration.register_org_webhook(db_session, uuid.uuid4())
    assert calls == []


def test_an_org_with_no_recording_policy_is_not_recorded_in_pilot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    assert compliance.resolve_recording_policy(None, "+919876543210").records_automatically is True  # unchanged elsewhere
    _pilot(monkeypatch)
    policy = compliance.resolve_recording_policy(None, "+919876543210")
    assert policy.mode == "off" and policy.records_automatically is False
    chosen = compliance.resolve_recording_policy({"recording_mode": "notice"}, "+919876543210")
    assert chosen.mode == "notice"  # an explicit choice by the org is respected


def test_an_invalid_stored_policy_means_no_recording_in_pilot_mode_and_the_legacy_default_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    assert compliance.resolve_recording_policy({"retention_days": "soon"}, "+919876543210") == compliance.LEGACY_POLICY
    _pilot(monkeypatch)
    assert compliance.resolve_recording_policy({"retention_days": "soon"}, "+919876543210").records_automatically is False


# --- the read-only verifier refuses anything that could write -----------------------------------------------------------------------------------------

import importlib.util
import sys as _sys

_spec = importlib.util.spec_from_file_location("pilot_verify_remote", ROOT / "scripts" / "pilot_verify_remote.py")
verifier = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_sys.modules["pilot_verify_remote"] = verifier
_spec.loader.exec_module(verifier)  # type: ignore[union-attr]


@pytest.mark.parametrize("sql", ["select 1", "SELECT count(*) FROM orgs WHERE name ~* '(test|pilot)'", "show transaction_read_only", "select max(created_at) from leads;"])
def test_the_verifier_accepts_plain_selects(sql: str) -> None:
    assert verifier.ro_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "insert into orgs values (1)", "update orgs set name = 'x'", "delete from users", "drop table orgs", "truncate leads",
        "alter table orgs add column x int", "create table t (id int)", "select 1; delete from users", "with d as (delete from users returning 1) select * from d",
        "select pg_sleep(1); select 1", "copy orgs to stdout", "grant all on orgs to public", "call something()", "do $$ begin end $$",
        "set transaction read write", "begin", "vacuum",
    ],
)
def test_the_verifier_refuses_anything_that_is_not_a_single_read(sql: str) -> None:
    with pytest.raises(verifier.NotReadOnly):
        verifier.ro_sql(sql)


class _RecordingRedis:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def execute_command(self, *args: Any) -> Any:
        self.sent.append(args)
        return 0


@pytest.mark.parametrize(
    "command",
    [("SET", "k", "v"), ("DEL", "k"), ("LPOP", "human_handoff_queue"), ("RPOP", "q"), ("FLUSHDB",), ("FLUSHALL",), ("EVAL", "return 1", 0),
     ("EXPIRE", "k", 1), ("INCR", "k"), ("RPUSH", "q", "x"), ("CLIENT", "KILL", "x"), ("CONFIG", "SET", "a", "b"), ("PUBLISH", "c", "m"),
     ("GET", "session:abc"), ("GET", "veerox:errors:2026-01-01")],
)
async def test_the_redis_wrapper_refuses_writes_and_reads_of_anything_secret(command: tuple) -> None:
    fake = _RecordingRedis()
    with pytest.raises(verifier.NotReadOnly):
        await verifier.ReadOnlyRedis(fake).cmd(*command)
    assert fake.sent == []  # nothing reached the server


async def test_the_redis_wrapper_passes_the_read_commands_and_the_kill_switch_read() -> None:
    fake = _RecordingRedis()
    wrapper = verifier.ReadOnlyRedis(fake)
    for command in (("PING",), ("DBSIZE",), ("SCAN", "0", "COUNT", 10), ("EXISTS", "a"), ("LLEN", "human_handoff_queue"), ("INFO", "stats"), ("GET", "veerox:kill_switch")):
        await wrapper.cmd(*command)
    assert len(fake.sent) == 7


def test_the_verifier_needs_its_explicit_flag(tmp_path: Any) -> None:
    with pytest.raises(SystemExit):
        verifier.main([str(tmp_path / "x.env")])


# --- Pilot Org setup, recording explicitly off ----------------------------------------------------------------------------------------------------------

from apps.api import pilot_setup


async def test_the_pilot_org_setup_refuses_to_run_outside_pilot_mode(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    with pytest.raises(RuntimeError, match="ENVIRONMENT=pilot"):
        await pilot_setup.apply(db_session)
    assert (await db_session.execute(select(Org))).scalars().all() == []  # nothing was created


async def test_the_pilot_org_is_created_with_recording_explicitly_off_and_nothing_else(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    org_id = "00000000-0000-0000-0000-0000000000c9"
    monkeypatch.setattr(settings, "default_org_id", org_id)
    monkeypatch.setattr(settings, "plivo_phone_number", "+911234567890")
    monkeypatch.setattr(settings, "twilio_phone_number", "")
    monkeypatch.setattr(settings, "meta_phone_number_id", "")
    result = await pilot_setup.apply(db_session)
    assert result["org_exists"] and result["renamed"] and result["is_named_pilot"] is True
    assert result["recording_mode"] == "off" and result["recording_explicitly_off"] is True
    assert result["phone_number_rows"] == {"plivo": 1}
    orgs = (await db_session.execute(select(Org))).scalars().all()
    assert [str(o.id) for o in orgs] == [org_id]  # exactly one org
    assert compliance.resolve_recording_policy(orgs[0].voice_compliance, "+919876543210").records_automatically is False


async def test_the_setup_is_idempotent_and_never_renames_an_operator_chosen_org(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    org_id = uuid.UUID("00000000-0000-0000-0000-0000000000c8")
    monkeypatch.setattr(settings, "default_org_id", str(org_id))
    monkeypatch.setattr(settings, "plivo_phone_number", "")
    monkeypatch.setattr(settings, "twilio_phone_number", "")
    monkeypatch.setattr(settings, "meta_phone_number_id", "")
    db_session.add(Org(id=org_id, name="Chosen By Operator", voice_compliance={"recording_mode": "always"}))
    await db_session.commit()
    first = await pilot_setup.apply(db_session)
    second = await pilot_setup.apply(db_session)
    assert first["renamed"] is False and second["renamed"] is False and second["is_named_pilot"] is False
    assert second["recording_mode"] == "off"  # the explicit off replaces an 'always' on the pilot org only
    assert len((await db_session.execute(select(Org))).scalars().all()) == 1


async def test_describe_is_read_only_and_reports_no_personal_data(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "default_org_id", "00000000-0000-0000-0000-0000000000c7")
    assert await pilot_setup.describe(db_session) == {"org_exists": False}
    assert (await db_session.execute(select(Org))).scalars().all() == []


def test_the_setup_script_refuses_apply_unless_every_offline_condition_holds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("pilot_setup_org", ROOT / "scripts" / "pilot_setup_org.py")
    script = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(script)  # type: ignore[union-attr]
    pilot_file, main_file = tmp_path / "pilot.env", tmp_path / "main.env"
    main_file.write_text("DATABASE_URL=postgresql+asyncpg://u:p@main-db.example.net/neondb\nREDIS_URL=rediss://d:p@main-r.example.net:6379\nPUBLIC_BASE_URL=https://m.ngrok-free.dev\n")
    pilot_file.write_text(
        "ENVIRONMENT=pilot\nPILOT_DATABASE_NAME=neondb\nDATABASE_URL=postgresql+asyncpg://u:p@main-db.example.net/neondb\n"
        "REDIS_URL=rediss://d:p@new-r.example.net:6379\nPUBLIC_BASE_URL=https://p.ngrok-free.dev\nPILOT_ALLOWED_NUMBERS=+919876543210\n"
    )
    monkeypatch.delenv("VEEROX_ENV_FILE", raising=False)
    problems = script.refusals(pilot_file, main_file)
    assert "VEEROX_ENV_FILE must point at the pilot file in this shell" in problems
    assert "preflight: database differs from main .env" in problems  # same server and name as the main database
    monkeypatch.setenv("VEEROX_ENV_FILE", str(pilot_file))
    assert "VEEROX_ENV_FILE must point at the pilot file in this shell" not in script.refusals(pilot_file, main_file)
    assert script.expected_head() == "a7c9e1f3b5d2"


# --- inbound WhatsApp in pilot mode --------------------------------------------------------------------------------------------------------------------

from types import SimpleNamespace

from apps.api.channels.whatsapp import adapter as wa_adapter


async def test_a_whatsapp_message_from_a_stranger_is_dropped_before_any_side_effect_in_pilot_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    touched: list[str] = []
    monkeypatch.setattr(wa_adapter, "_extract_message", lambda payload: SimpleNamespace(id="wamid.1", from_phone="919111111111"))

    async def claim(msg_id: str) -> bool:
        touched.append("claim")
        return True

    monkeypatch.setattr(wa_adapter, "_claim_message_id", claim)
    await wa_adapter.process_inbound({"entry": []})
    assert touched == []  # not even the idempotency key was written


async def test_an_allowlisted_sender_proceeds_past_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch, numbers="+919876543210")
    touched: list[str] = []
    monkeypatch.setattr(wa_adapter, "_extract_message", lambda payload: SimpleNamespace(id="wamid.2", from_phone="919876543210"))

    async def claim(msg_id: str) -> bool:
        touched.append("claim")
        return False  # stop right after the guard: a duplicate, so nothing else runs

    monkeypatch.setattr(wa_adapter, "_claim_message_id", claim)
    await wa_adapter.process_inbound({"entry": []})
    assert touched == ["claim"]


async def test_outside_pilot_mode_the_inbound_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    touched: list[str] = []
    monkeypatch.setattr(wa_adapter, "_extract_message", lambda payload: SimpleNamespace(id="wamid.3", from_phone="919111111111"))

    async def claim(msg_id: str) -> bool:
        touched.append("claim")
        return False

    monkeypatch.setattr(wa_adapter, "_claim_message_id", claim)
    await wa_adapter.process_inbound({"entry": []})
    assert touched == ["claim"]


# --- no public API documentation in pilot mode ---------------------------------------------------------------------------------------------------------

async def _docs_statuses() -> tuple[int, int, int]:
    app = main_module.create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return ((await c.get("/docs")).status_code, (await c.get("/redoc")).status_code, (await c.get("/openapi.json")).status_code)


async def test_pilot_mode_publishes_no_api_documentation(monkeypatch: pytest.MonkeyPatch) -> None:
    _pilot(monkeypatch)
    assert await _docs_statuses() == (404, 404, 404)


async def test_outside_pilot_mode_the_documentation_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "dev")
    docs, redoc, schema = await _docs_statuses()
    assert (docs, redoc, schema) == (200, 200, 200)
