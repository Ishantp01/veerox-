"""Phase 8H: Plivo webhook signatures (V3) and the media-stream token.

The reference signatures below were produced by Plivo's own SDK (plivo 4.63.0, ``plivo/utils/signature_v3.py``) for the
synthetic token and nonce in this file, so the assembly is pinned to Plivo's implementation, not to our reading of the docs.
Whether Plivo's servers sign our real requests this way is only provable on a live call; ``PLIVO_SIGNATURE_MODE=warn`` exists
to find out without rejecting anything.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import structlog

from apps.api.channels.voice import signature, webhook
from apps.api.channels.voice import realtime_bridge
from apps.api.config import settings
from apps.api.core.org_credentials import PlivoCredentials
from apps.api.tests.test_admin_endpoints import ORG_ID, client  # noqa: F401

TOKEN = "unit-test-token-0123456789abcdef"
NONCE = "1234567890123456789"
BASE = "https://pilot.example.ngrok-free.dev"
OWNER = "+911234567890"
LINE = "+910000000001"

# (method, url, post params) -> signature produced by Plivo's SDK
VECTORS = {
    "post_query_and_params": ("POST", BASE + "/voice/answer?org_id=00000000-0000-0000-0000-000000000001",
                              {"CallUUID": "c1d2e3", "From": "+910000000001", "To": "+911234567890", "Direction": "outbound", "Event": "StartApp", "CallStatus": "in-progress"},
                              "8LhNkaO2hfxIwwyp5SsPfQseB9pveAuVexdkwkQWzso="),
    "post_params_only": ("POST", BASE + "/voice/answer", {"CallUUID": "c1d2e3", "To": "+911234567890", "From": "+910000000001"},
                         "HwLukScvb4s2AO7l18b7os9u9PEin2rTdesXEXRaLic="),
    "post_query_no_params": ("POST", BASE + "/voice/campaign-hangup?campaign_target_id=abc&z=1", {},
                             "r8Wf6/yj2ecC1mfCKc3Tz5UcQ1lJ0CKNpuv0yPKFCJs="),
    "post_unsorted_query": ("POST", BASE + "/voice/answer?org_id=O&b=2&a=1", {"CallUUID": "x", "Zeta": "last", "Alpha": "first"},
                            "N9lKne4ptVByUWaHz7awsMR4gimwq8u3Tmeav86IsUY="),
    "post_special_chars": ("POST", BASE + "/voice/recording-callback?org_id=O",
                           {"CallUUID": "x", "RecordingUrl": "https://media.example/r.mp3?a=1&b=2", "Note": "namaste ji, 100% theek"},
                           "EUtaWoB1oqEO5m8XUk1R/W+S+JhDG/bLUUfcyL8XADQ="),
    "get_query": ("GET", BASE + "/voice/answer?org_id=O&CallUUID=c1&To=%2B911234567890", {},
                  "TDFwJWgdM4iXEmh1yxVIPtVi6aLjBb5tsVk28T/F5XA="),
}


def _body(params: dict[str, str]) -> bytes:
    return urlencode(params).encode()


def _verify(method: str, url: str, params: dict[str, str], sig: str, *, nonce: str | None = NONCE, tokens: list[str] | None = None) -> signature.Verdict:
    return signature.verify_plivo_request(
        method=method, url=url, body=_body(params), signature_headers=[sig] if sig else [], nonce=nonce,
        tokens=[TOKEN] if tokens is None else tokens,
    )


# --- the algorithm, pinned to Plivo's SDK ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(VECTORS))
def test_our_signature_matches_the_one_plivos_sdk_produces(name: str) -> None:
    method, url, params, expected = VECTORS[name]
    assert signature.compute_signature(TOKEN, url, method, NONCE, {k: [v] for k, v in params.items()}) == expected


@pytest.mark.parametrize("name", list(VECTORS))
def test_a_genuine_request_verifies(name: str) -> None:
    method, url, params, expected = VECTORS[name]
    assert _verify(method, url, params, expected) == signature.Verdict(True, "ok")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("param_value", "mismatch"), ("extra_param", "mismatch"), ("dropped_param", "mismatch"), ("query_value", "mismatch"),
        ("path", "mismatch"), ("host", "mismatch"), ("scheme", "mismatch"), ("wrong_token", "mismatch"), ("wrong_nonce", "mismatch"),
        ("no_signature", "no_signature_header"), ("no_nonce", "no_nonce"), ("no_token", "no_token_configured"),
    ],
)
def test_every_kind_of_forgery_is_refused(change: str, reason: str) -> None:
    method, url, params, sig = VECTORS["post_query_and_params"]
    params, kwargs = dict(params), {}
    if change == "param_value":
        params["To"] = "+919999999999"
    elif change == "extra_param":
        params["Injected"] = "1"
    elif change == "dropped_param":
        params.pop("Event")
    elif change == "query_value":
        url = url.replace("0001", "0002")
    elif change == "path":
        url = url.replace("/voice/answer", "/voice/campaign-hangup")
    elif change == "host":
        url = url.replace("pilot.example", "evil.example")
    elif change == "scheme":
        url = url.replace("https://", "http://")
    elif change == "wrong_token":
        kwargs["tokens"] = ["some-other-token"]
    elif change == "wrong_nonce":
        kwargs["nonce"] = "999"
    elif change == "no_signature":
        sig = ""
    elif change == "no_nonce":
        kwargs["nonce"] = None
    elif change == "no_token":
        kwargs["tokens"] = []
    assert _verify(method, url, params, sig, **kwargs) == signature.Verdict(False, reason)


def test_a_header_with_several_signatures_is_accepted_if_one_matches() -> None:
    method, url, params, sig = VECTORS["post_params_only"]
    assert _verify(method, url, params, f"AAAA,{sig}, BBBB").ok


def test_a_signature_for_a_request_without_a_body_cannot_be_reused_to_carry_one() -> None:
    method, url, params, sig = VECTORS["get_query"]  # by Plivo's algorithm this base equals a POST with the same query and no body
    assert not signature.verify_plivo_request(method="POST", url=url, body=b"To=%2B919999999999", signature_headers=[sig], nonce=NONCE, tokens=[TOKEN]).ok


# --- modes ----------------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environment", "configured", "expected"),
    [
        ("pilot", "auto", "enforce"), ("pilot", "", "enforce"), ("pilot", "garbage", "enforce"), ("pilot", "off", "off"), ("pilot", "warn", "warn"),
        ("dev", "auto", "off"), ("production", "auto", "off"), ("production", "garbage", "off"), ("production", "enforce", "enforce"),
        ("production", "WARN", "warn"),
    ],
)
def test_the_mode_defaults_to_enforce_in_pilot_and_stays_off_elsewhere(monkeypatch: pytest.MonkeyPatch, environment: str, configured: str, expected: str) -> None:
    monkeypatch.setattr(settings, "environment", environment)
    monkeypatch.setattr(settings, "plivo_signature_mode", configured)
    assert signature.effective_mode() == expected


# --- the endpoints ----------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def pilot_webhooks(monkeypatch: pytest.MonkeyPatch, test_engine: Any) -> dict[str, list[Any]]:
    monkeypatch.setattr(settings, "environment", "pilot")
    monkeypatch.setattr(settings, "plivo_signature_mode", "auto")
    monkeypatch.setattr(settings, "pilot_allowed_numbers", OWNER)
    monkeypatch.setattr(settings, "plivo_auth_token", TOKEN)
    monkeypatch.setattr(settings, "public_base_url", BASE)
    from sqlalchemy.ext.asyncio import async_sessionmaker
    monkeypatch.setattr(webhook, "AsyncSessionLocal", async_sessionmaker(bind=test_engine, expire_on_commit=False))
    from apps.api.tests.test_pilot_8i_gate import ReplayStore
    store = ReplayStore()
    monkeypatch.setattr(signature, "get_redis_pool", lambda: store)
    effects: dict[str, list[Any]] = {"precall": [], "ended": [], "saved": []}
    monkeypatch.setattr(webhook, "start_precall_connect", lambda *a, **k: effects["precall"].append(a))

    async def ended(target: str) -> None:
        effects["ended"].append(target)

    async def saved(*a: Any) -> bool:
        effects["saved"].append(a)
        return True

    monkeypatch.setattr(webhook.campaign_dialer, "handle_call_ended", ended)
    monkeypatch.setattr(webhook.voice_adapter, "save_call_recording", saved)
    return effects


def _signed(path_and_query: str, params: dict[str, str], token: str = TOKEN, method: str = "POST") -> dict[str, Any]:
    url = BASE + path_and_query
    nonce = uuid.uuid4().hex
    sig = signature.compute_signature(token, url, method, nonce, {k: [v] for k, v in params.items()})
    return {"headers": {"X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": nonce}, "content": _body(params)}


def _post(client: Any, path_and_query: str, params: dict[str, str], **extra: Any) -> Any:
    kw = _signed(path_and_query, params) if not extra.pop("unsigned", False) else {"content": _body(params)}
    kw.setdefault("headers", {})
    kw["headers"] = {**kw["headers"], "Content-Type": "application/x-www-form-urlencoded", **extra.pop("headers", {})}
    return client.post(path_and_query, **kw)


OUTBOUND = {"CallUUID": "u1", "From": LINE, "To": OWNER, "Direction": "outbound"}


async def test_a_genuine_outbound_answer_is_served_and_carries_a_stream_token(client: Any, pilot_webhooks: dict[str, list[Any]]) -> None:
    ticketed = await signature.attach_call_ticket(BASE + f"/voice/answer?org_id={ORG_ID}", OWNER)  # minted when we dialled
    response = await _post(client, ticketed[len(BASE):], OUTBOUND)
    assert response.status_code == 200 and "<Stream" in response.text
    stream_url = response.text.split('streamTimeout="3600">')[1].split("</Stream>")[0].replace("&amp;", "&")
    query = {k: v[0] for k, v in parse_qs(urlsplit(stream_url).query).items()}
    assert query["from"] == OWNER and query["call_uuid"] == "u1" and query["org_id"] == str(ORG_ID)
    assert signature.stream_token("u1", OWNER, str(ORG_ID), None, "plivo", int(query["exp"])) == query["sig"]  # round-trips through the URL
    assert len(pilot_webhooks["precall"]) == 1


@pytest.mark.parametrize("how", ["unsigned", "wrong_token", "tampered_body", "tampered_query", "twilio_shaped", "replayed_on_other_route"])
async def test_a_forged_answer_is_refused_before_anything_happens(client: Any, pilot_webhooks: dict[str, list[Any]], how: str) -> None:
    path = f"/voice/answer?org_id={ORG_ID}"
    if how == "unsigned":
        response = await _post(client, path, OUTBOUND, unsigned=True)
    elif how == "wrong_token":
        kw = _signed(path, OUTBOUND, token="not-the-token")
        response = await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    elif how == "tampered_body":
        kw = _signed(path, OUTBOUND)
        response = await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=_body({**OUTBOUND, "To": "+919999999999"}))
    elif how == "tampered_query":
        kw = _signed(path, OUTBOUND)
        response = await client.post(f"/voice/answer?org_id={uuid.uuid4()}", headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    elif how == "twilio_shaped":
        response = await _post(client, path, {"CallSid": "CA1", "AccountSid": "AC1", "From": LINE, "To": OWNER}, unsigned=True)
    else:
        kw = _signed("/voice/campaign-hangup?campaign_target_id=t1", OUTBOUND)
        response = await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    assert response.status_code == 403 and response.content == b""  # nothing explained to the sender
    assert pilot_webhooks["precall"] == []  # no AI session was warmed up, no usage written


async def test_the_other_webhooks_need_a_signature_too(client: Any, pilot_webhooks: dict[str, list[Any]]) -> None:
    assert (await _post(client, "/voice/campaign-hangup?campaign_target_id=t1", {"CallUUID": "u1"}, unsigned=True)).status_code == 403
    assert (await _post(client, "/voice/recording-callback", {"CallUUID": "u1", "RecordingUrl": "https://x/r.mp3"}, unsigned=True)).status_code == 403
    assert pilot_webhooks["ended"] == [] and pilot_webhooks["saved"] == []
    assert (await _post(client, "/voice/campaign-hangup?campaign_target_id=t1", {"CallUUID": "u1", "CallStatus": "completed"})).status_code == 204
    assert (await _post(client, "/voice/recording-callback", {"CallUUID": "u1", "RecordingUrl": "https://x/r.mp3"})).status_code == 204
    assert pilot_webhooks["ended"] == ["t1"] and len(pilot_webhooks["saved"]) == 1


async def test_a_main_account_signature_is_accepted_too(client: Any, pilot_webhooks: dict[str, list[Any]]) -> None:
    kw = _signed("/voice/campaign-hangup?campaign_target_id=t1", {"CallUUID": "u1"})
    response = await client.post(
        "/voice/campaign-hangup?campaign_target_id=t1",
        headers={"X-Plivo-Signature-Ma-V3": kw["headers"]["X-Plivo-Signature-V3"], "X-Plivo-Signature-V3-Nonce": kw["headers"]["X-Plivo-Signature-V3-Nonce"], "Content-Type": "application/x-www-form-urlencoded"},
        content=kw["content"],
    )
    assert response.status_code == 204


async def test_an_org_with_its_own_plivo_account_is_checked_against_its_own_token(client: Any, pilot_webhooks: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch, test_engine: Any) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    monkeypatch.setattr(webhook, "AsyncSessionLocal", async_sessionmaker(bind=test_engine, expire_on_commit=False))
    monkeypatch.setattr(webhook, "resolve_plivo_credentials", lambda org: PlivoCredentials(auth_id="x", auth_token="org-own-token"))
    path = f"/voice/campaign-hangup?org_id={ORG_ID}&campaign_target_id=t1"
    kw = _signed(path, {"CallUUID": "u1"}, token="org-own-token")
    ok = await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    assert ok.status_code == 204
    kw = _signed(path, {"CallUUID": "u1"}, token="a-third-token")
    bad = await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    assert bad.status_code == 403


async def test_warn_mode_lets_everything_through_and_off_mode_changes_nothing(client: Any, pilot_webhooks: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    for mode in ("warn", "off"):
        monkeypatch.setattr(settings, "plivo_signature_mode", mode)
        with structlog.testing.capture_logs() as logs:
            response = await _post(client, "/voice/campaign-hangup?campaign_target_id=t1", {"CallUUID": "u1"}, unsigned=True)
        assert response.status_code == 204
        assert any(entry["event"] == "voice_webhook_would_reject" for entry in logs) == (mode == "warn")


async def test_outside_pilot_mode_nothing_changes_by_default(client: Any, pilot_webhooks: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    response = await _post(client, "/voice/campaign-hangup?campaign_target_id=t1", {"CallUUID": "u1"}, unsigned=True)
    assert response.status_code == 204  # an existing deployment keeps its behaviour until it opts in


async def test_a_rejection_logs_a_reason_code_and_no_secret(client: Any, pilot_webhooks: dict[str, list[Any]]) -> None:
    path = "/voice/campaign-hangup?campaign_target_id=t1"
    kw = _signed(path, {"CallUUID": "u1"}, token="not-the-token")
    with structlog.testing.capture_logs() as logs:
        await client.post(path, headers={**kw["headers"], "Content-Type": "application/x-www-form-urlencoded"}, content=kw["content"])
    rejected = [entry for entry in logs if entry["event"] == "voice_webhook_rejected"]
    assert len(rejected) == 1 and rejected[0]["reason"] == "mismatch" and rejected[0]["route"] == "campaign_hangup"
    assert rejected[0]["host_matches_public_base"] is False  # the test client's host is not the public one; only the yes/no is logged
    blob = repr(logs)
    for secret in (TOKEN, "not-the-token", NONCE, kw["headers"]["X-Plivo-Signature-V3"], "u1"):
        assert secret not in blob


# --- the media stream ---------------------------------------------------------------------------------------------------------------------------


class _Socket:
    def __init__(self, params: dict[str, str]) -> None:
        self.query_params = params
        self.closed_with: int | None = None

    async def accept(self, *a: Any, **k: Any) -> None:
        return None

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    async def receive_text(self) -> str:
        raise RuntimeError("must not read from a rejected stream")


async def test_a_stream_token_is_bound_to_everything_it_was_issued_for(pilot_webhooks: dict[str, list[Any]]) -> None:
    expiry = str(int(signature.time.time()) + 300)
    token = signature.stream_token("u1", OWNER, "org-1", None, "plivo", int(expiry))
    assert await signature.stream_allowed(token, expiry, "u1", OWNER, "org-1", None, "plivo")
    for args in (("u2", OWNER, "org-1", None, "plivo"), ("u1", "+919999999999", "org-1", None, "plivo"), ("u1", OWNER, "org-2", None, "plivo"),
                 ("u1", OWNER, "org-1", "t1", "plivo"), ("u1", OWNER, "org-1", None, "twilio")):
        assert not await signature.stream_allowed(token, expiry, *args)
    assert not await signature.stream_allowed(None, expiry, "u1", OWNER, "org-1", None, "plivo")
    assert not await signature.stream_allowed("0" * 40, expiry, "u1", OWNER, "org-1", None, "plivo")


async def test_stream_tokens_are_not_accepted_when_the_key_is_unavailable_and_never_when_off(monkeypatch: pytest.MonkeyPatch, pilot_webhooks: dict[str, list[Any]]) -> None:
    monkeypatch.setattr(settings, "plivo_signature_mode", "off")
    assert await signature.stream_allowed(None, None, "u1", OWNER, None, None, "plivo")  # legacy: no token needed
    monkeypatch.setattr(settings, "plivo_signature_mode", "warn")
    assert await signature.stream_allowed(None, None, "u1", OWNER, None, None, "plivo")  # logged, not blocked


async def test_a_stream_without_a_valid_token_is_closed_before_any_ai_session_is_opened(pilot_webhooks: dict[str, list[Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    warmed: list[str] = []
    monkeypatch.setattr(realtime_bridge, "_connect_to_openai", lambda *a, **k: warmed.append("openai"))
    # the allowlisted number alone, which is all someone who knew it would have, is not enough
    ws = _Socket({"from": OWNER, "call_uuid": "u1", "provider": "plivo", "org_id": str(ORG_ID)})
    await realtime_bridge.voice_stream(ws)  # type: ignore[arg-type]
    assert ws.closed_with == 1008 and warmed == []
    forged = _Socket({"from": OWNER, "call_uuid": "u1", "provider": "plivo", "org_id": str(ORG_ID), "sig": "f" * 40})
    await realtime_bridge.voice_stream(forged)  # type: ignore[arg-type]
    assert forged.closed_with == 1008 and warmed == []
