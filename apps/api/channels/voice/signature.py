"""Who is allowed to talk to the voice webhooks and the media stream.

Plivo signs every request it sends to an answer, hangup or callback URL (``X-Plivo-Signature-V3`` with a nonce in
``X-Plivo-Signature-V3-Nonce``; ``X-Plivo-Signature-Ma-V3`` is signed with the main account's token). The signature is
``base64(HMAC-SHA256(auth_token, base + "." + nonce))`` where ``base`` is the URL Plivo was given (scheme, host, path and
the query string with its parameters sorted), then, for a POST, the form parameters sorted by name and concatenated as
``name + value``. See https://www.plivo.com/docs/voice/concepts/signature-validation . The assembly below mirrors the
published algorithm (including the "." it puts between the query string and the POST parameters), and the tests pin it to
signatures produced by Plivo's own SDK.

The URL is rebuilt from ``PUBLIC_BASE_URL`` plus the request path and query, never from ``request.url``: behind a tunnel or
a reverse proxy the server sees http and an internal host, while Plivo signed the public https URL it was given.

The media stream is a WebSocket and Plivo cannot sign it, so our own answer webhook (once it has verified the request)
puts a short HMAC token in the stream URL it returns, and the stream endpoint checks it. Without that, anyone who knew a
call id and an allowlisted number could open the stream directly and make the server spend on OpenAI.

Modes (``PLIVO_SIGNATURE_MODE``): ``off`` (legacy), ``warn`` (log what would be rejected, reject nothing), ``enforce``
(reject), ``auto`` (the default): ``enforce`` in pilot mode, ``off`` elsewhere, so existing deployments are unchanged until
they are switched on deliberately. Nothing here logs a signature, nonce, header, token or request body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import structlog
from fastapi import Request
from fastapi.responses import Response

from apps.api import pilot
from apps.api.config import settings
from apps.api.redis_client import get_redis_pool

logger = structlog.get_logger(__name__)

MODES = ("auto", "off", "warn", "enforce")


def effective_mode() -> str:
    """``off`` | ``warn`` | ``enforce``. An unrecognised value fails closed in pilot mode and stays legacy elsewhere."""
    configured = str(getattr(settings, "plivo_signature_mode", "auto") or "auto").strip().lower()
    if configured in ("off", "warn", "enforce"):
        return configured
    return "enforce" if pilot.pilot_mode() else "off"


# --- the published signature algorithm ---------------------------------------------------------------------------------------------


def _sorted_query(query: str) -> str:
    parsed = parse_qs(query, keep_blank_values=True)
    return "&".join(
        "&".join(f"{key}={value}" for value in sorted(parsed[key])) for key in sorted(parsed)
    )


def _sorted_params(params: dict[str, list[str]]) -> str:
    return "".join(f"{key}{value}" for key in sorted(params) for value in sorted(params[key]))


def base_string(url: str, method: str, post_params: dict[str, list[str]] | None = None) -> str:
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    query = _sorted_query(parts.query)
    if method.upper() == "GET":
        return base + ("?" + query if query else "")
    params = post_params or {}
    if query or params:
        base += "?" + query
    if query and params:
        base += "."
    return base + _sorted_params(params)


def compute_signature(
    auth_token: str,
    url: str,
    method: str,
    nonce: str,
    post_params: dict[str, list[str]] | None = None,
) -> str:
    message = f"{base_string(url, method, post_params)}.{nonce}".encode()
    return base64.b64encode(
        hmac.new(auth_token.encode(), message, hashlib.sha256).digest()
    ).decode()


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str  # a fixed code, safe to log


def verify_plivo_request(
    *,
    method: str,
    url: str,
    body: bytes,
    signature_headers: list[str],
    nonce: str | None,
    tokens: list[str],
) -> Verdict:
    signatures = [s.strip() for header in signature_headers for s in header.split(",") if s.strip()]
    if not signatures:
        return Verdict(False, "no_signature_header")
    if not nonce:
        return Verdict(False, "no_nonce")
    if not tokens:
        return Verdict(False, "no_token_configured")
    post_params = (
        parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        if method.upper() == "POST"
        else {}
    )
    for token in tokens:
        expected = compute_signature(token, url, method, nonce, post_params)
        if any(_same(expected, given) for given in signatures):
            return Verdict(True, "ok")
    return Verdict(False, "mismatch")


def _same(expected: str, given: str) -> bool:
    """Constant-time comparison that cannot raise on non-ASCII input."""
    return hmac.compare_digest(expected.encode(), given.encode("utf-8", "replace"))


_HINT_FIELDS = {"CallStatus": "plivo_call_status", "Event": "plivo_event", "Direction": "plivo_direction", "HangupCause": "plivo_hangup_cause"}
_HINT_VALUE = re.compile(r"[A-Za-z0-9_ -]{1,32}")


def provider_hints(body: bytes) -> dict[str, str]:
    """The few status words Plivo puts in a callback (call status, event, direction, hangup cause), to tell on a rejection
    whether a request was the provider's own end-of-call notice. Only short plain words are kept: never a number, id,
    URL or anything a caller said. Never raises."""
    try:
        params = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        return {
            label: value
            for field, label in _HINT_FIELDS.items()
            if (value := _first(params, field)) and _HINT_VALUE.fullmatch(value) and not re.search(r"\d{4,}", value)
        }
    except Exception:  # noqa: BLE001
        return {}


def public_url(request: Request) -> str:
    """The URL Plivo called, as we gave it to Plivo: the public base plus this request's path and query."""
    url = settings.public_base_url.rstrip("/") + request.url.path
    return url + ("?" + request.url.query if request.url.query else "")


async def guard(
    request: Request, route: str, extra_tokens: Callable[[], Awaitable[list[str]]] | None = None
) -> Response | None:
    """``None`` to carry on, or the 403 to return. Never raises."""
    mode = effective_mode()
    if mode == "off":
        return None
    hints: dict[str, str] = {}
    try:
        headers = request.headers
        signature_headers = [
            h
            for h in (headers.get("x-plivo-signature-v3"), headers.get("x-plivo-signature-ma-v3"))
            if h
        ]
        nonce = headers.get("x-plivo-signature-v3-nonce")
        body = await request.body() if request.method.upper() == "POST" else b""
        hints = provider_hints(body)
        url = public_url(request)
        tokens = [t for t in (settings.plivo_auth_token,) if t]
        verdict = verify_plivo_request(
            method=request.method,
            url=url,
            body=body,
            signature_headers=signature_headers,
            nonce=nonce,
            tokens=tokens,
        )
        if not verdict.ok and extra_tokens is not None:
            more = [t for t in await extra_tokens() if t and t not in tokens]
            if more:
                verdict = verify_plivo_request(
                    method=request.method,
                    url=url,
                    body=body,
                    signature_headers=signature_headers,
                    nonce=nonce,
                    tokens=more,
                )
    except Exception:  # noqa: BLE001 - a verification fault must not become an open door in enforce mode
        verdict = Verdict(False, "error")
    if verdict.ok:
        # An answer to a call WE placed is authorised by the single-use ticket minted when we dialled (its freshness is
        # ours, so it needs no epoch of remembered nonces). Everything else (inbound answers, status and recording
        # callbacks) is checked against the replay ledger, which stays fail-closed until it has been initialised.
        if route == "answer" and is_outbound_answer(request):
            verdict = await consume_call_ticket(request, body)
        else:
            verdict = await claim_nonce(nonce or "")
        if verdict.ok:
            return None
    # Whether the request arrived on the host PUBLIC_BASE_URL names, as a yes/no: the usual reason a genuine request fails
    # is a PUBLIC_BASE_URL that is not the address Plivo was given. The host itself is not logged.
    host_ok = (request.headers.get("host") or "").lower() == urlsplit(
        settings.public_base_url
    ).netloc.lower()
    if mode == "enforce":
        logger.warning(
            "voice_webhook_rejected",
            route=route,
            reason=verdict.reason,
            host_matches_public_base=host_ok,
            **hints,
        )
        return Response(status_code=403)
    logger.warning(
        "voice_webhook_would_reject",
        route=route,
        reason=verdict.reason,
        host_matches_public_base=host_ok,
        **hints,
    )
    return None


# No TTL: Plivo's random nonce has no authenticated timestamp. A TTL would
# reauthorize a captured request. A fixed-capacity ledger fails closed when full
# or absent. Its lifetime is the signing-credential epoch, not process uptime.
NONCE_CLAIM = """-- plivo_nonce_v2
if redis.call('SISMEMBER', KEYS[1], '__ready__') == 0 then return -1 end
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then return 0 end
if redis.call('SCARD', KEYS[1]) >= tonumber(ARGV[2]) + 1 then return -2 end
redis.call('SADD', KEYS[1], ARGV[1])
return 1
"""
STREAM_CLAIM = """-- plivo_stream_v2
if redis.call('HGET', KEYS[1], '__ready__') ~= '1' then return -1 end
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then return 0 end
if redis.call('HLEN', KEYS[1]) >= tonumber(ARGV[3]) + 1 then return -2 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""
STREAM_RELEASE = """-- plivo_release_v2
if redis.call('HGET', KEYS[1], ARGV[1]) ~= ARGV[2] then return 0 end
redis.call('HDEL', KEYS[1], ARGV[1])
return 1
"""


def strict_state() -> bool:
    """Pilot mode keeps the permanent, fixed-capacity nonce ledger and non-expiring stream ownership (they need a one-time
    initialisation and fail closed when full or lost). Every other environment uses EXPIRING state: a nonce is remembered
    for ``PLIVO_NONCE_EXPIRING_TTL_SECONDS`` and a stream lease lives ``PLIVO_STREAM_LEASE_SECONDS`` (longer than any call
    can last). That needs no initialisation, cannot fill up, and cleans up after a crashed or redeployed process on its
    own; the price is that a captured, already-accepted request is replayable again after the nonce TTL, which Plivo's
    timestamp-free signatures cannot prevent. ``PLIVO_REPLAY_STATE`` = strict | expiring forces either."""
    configured = str(getattr(settings, "plivo_replay_state", "auto") or "auto").strip().lower()
    if configured in ("strict", "expiring"):
        return configured == "strict"
    return pilot.pilot_mode()


def state_key(kind: str) -> str:
    key = _stream_key()
    if key is None:
        raise ValueError("security_key_unavailable")
    return f"plivo:security:v2:{{{hashlib.sha256(key).hexdigest()}}}:{kind}"


async def claim_nonce(nonce: str) -> Verdict:
    try:
        fingerprint = hashlib.sha256(nonce.encode()).hexdigest()
        if not strict_state():
            stored = await get_redis_pool().set(
                state_key("nonce:" + fingerprint), "1", nx=True, ex=max(60, int(settings.plivo_nonce_expiring_ttl_seconds))
            )
            return Verdict(True, "ok") if stored else Verdict(False, "replayed_nonce")
        result = int(
            await get_redis_pool().eval(
                NONCE_CLAIM,
                1,
                state_key("nonces"),
                fingerprint,
                max(1, int(settings.plivo_nonce_capacity)),
            )
        )
        reason = {
            1: "ok",
            0: "replayed_nonce",
            -1: "nonce_state_missing",
            -2: "nonce_capacity",
        }.get(result, "nonce_store_error")
        return Verdict(result == 1, reason)
    except Exception:  # noqa: BLE001
        return Verdict(False, "nonce_store_error")


# --- single-use authorisation of the answer to an outbound call -------------------------------------------------------------------------


class CallTicketUnavailable(RuntimeError):
    """The store that makes an outbound call answerable could not be written, so the call must not be placed."""


def _first(query: dict[str, list[str]], name: str) -> str:
    return (query.get(name) or [""])[0]


def _digits10(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def _ticket_binding(org_id: str, campaign_target_id: str, to_phone: str) -> str:
    return hashlib.sha256(
        json.dumps([org_id, campaign_target_id, _digits10(to_phone)], separators=(",", ":")).encode()
    ).hexdigest()


def _ticket_key(ticket: str) -> str:
    return state_key("ticket:" + hashlib.sha256(ticket.encode()).hexdigest()[:40])


def is_outbound_answer(request: Request) -> bool:
    """Our own outbound answer URLs always name the org (or a campaign target); an inbound call's URL names neither."""
    query = parse_qs(request.url.query)
    return bool(_first(query, "org_id") or _first(query, "campaign_target_id"))


async def attach_call_ticket(answer_url: str, to_e164: str) -> str:
    """Mint a random single-use ticket for this call and return the answer URL carrying it (``ct``). Plivo signs the whole URL,
    so the ticket is covered by its signature. Stored with a short expiry, bound to org, campaign target and destination.
    Does nothing when signatures are off or the URL is not an outbound-answer URL. In enforce mode a store failure stops the
    call before anything is dialled (the call could not be answered anyway)."""
    mode = effective_mode()
    if mode == "off":
        return answer_url
    query = parse_qs(urlsplit(answer_url).query)
    org_id, campaign = _first(query, "org_id"), _first(query, "campaign_target_id")
    if not (org_id or campaign):
        return answer_url
    try:
        ticket = secrets.token_urlsafe(24)
        stored = await get_redis_pool().set(
            _ticket_key(ticket),
            _ticket_binding(org_id, campaign, to_e164),
            nx=True,
            ex=max(30, int(settings.plivo_call_ticket_ttl_seconds or 180)),
        )
        if not stored:
            raise CallTicketUnavailable("call_ticket_not_stored")
    except Exception as exc:  # noqa: BLE001
        if mode == "enforce":
            logger.warning("voice_call_ticket_unavailable", reason="ticket_store_error")
            raise CallTicketUnavailable("call_ticket_store_error") from exc
        logger.warning("voice_call_ticket_would_fail", reason="ticket_store_error")
        return answer_url
    return answer_url + ("&" if "?" in answer_url else "?") + "ct=" + ticket


async def consume_call_ticket(request: Request, body: bytes) -> Verdict:
    """Atomically take the ticket named in the URL. It exists once, for a few minutes, for one org and one destination: a
    repeat, an old request, or one for another destination finds nothing or a mismatch. Only signed requests reach here."""
    try:
        query = parse_qs(request.url.query)
        ticket = _first(query, "ct")
        if not ticket:
            return Verdict(False, "ticket_missing")
        to_phone = _first(parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True), "To")
        stored = await get_redis_pool().getdel(_ticket_key(ticket))
        if stored is None:
            return Verdict(False, "ticket_unknown_or_used")
        if isinstance(stored, bytes):
            stored = stored.decode()
        expected = _ticket_binding(_first(query, "org_id"), _first(query, "campaign_target_id"), to_phone)
        return Verdict(True, "ok") if _same(expected, str(stored)) else Verdict(False, "ticket_mismatch")
    except Exception:  # noqa: BLE001
        return Verdict(False, "ticket_store_error")


@dataclass(frozen=True, repr=False)
class StreamLease:
    key: str
    field: str
    owner: str
    expiring: bool = False  # a per-call key with an expiry (production) rather than a field in the strict hash (pilot)


STREAM_RELEASE_EXPIRING = """-- plivo_release_expiring_v1
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""


async def release_stream(lease: StreamLease | None) -> bool:
    if lease is None:
        return True
    try:
        if lease.expiring:
            return bool(await get_redis_pool().eval(STREAM_RELEASE_EXPIRING, 1, lease.key, lease.owner))
        return bool(
            await get_redis_pool().eval(STREAM_RELEASE, 1, lease.key, lease.field, lease.owner)
        )
    except Exception:  # noqa: BLE001
        # Keep ownership locked on failure. Never allow a replacement by guessing
        # that a timeout means the old owner is gone.
        logger.warning("voice_stream_release_failed", reason="stream_store_error")
        return False


# --- the media stream token ----------------------------------------------------------------------------------------------------------


def _stream_key() -> bytes | None:
    secret = settings.secret_encryption_key or settings.admin_token
    return hashlib.sha256(b"veerox-voice-stream|" + secret.encode()).digest() if secret else None


def stream_token(
    call_uuid: str,
    caller: str,
    org_id: str | None,
    campaign_target_id: str | None,
    provider: str,
    expires_at: int,
) -> str | None:
    """HMAC over the call, the other party, the org, the campaign target, the provider and the expiry time."""
    key = _stream_key()
    if key is None:
        return None
    message = json.dumps(
        [
            call_uuid or "",
            caller or "",
            org_id or "",
            campaign_target_id or "",
            provider or "",
            str(int(expires_at)),
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:40]


def issue_stream_params(
    call_uuid: str, caller: str, org_id: str | None, campaign_target_id: str | None, provider: str
) -> dict[str, str]:
    """The ``exp`` and ``sig`` query parameters for a stream URL, or nothing when signatures are off (so a deployment that has
    not opted in sends exactly the URL it always did)."""
    if effective_mode() == "off":
        return {}
    expires_at = int(time.time()) + int(settings.plivo_stream_token_ttl_seconds or 300)
    token = stream_token(call_uuid, caller, org_id, campaign_target_id, provider, expires_at)
    return {"exp": str(expires_at), "sig": token} if token else {}


class _Claimed(Exception):
    """Internal: carries the outcome of the expiring-lease claim out of the shared try block."""


async def stream_allowed(
    token: str | None,
    expires_at: str | None,
    call_uuid: str,
    caller: str,
    org_id: str | None,
    campaign_target_id: str | None,
    provider: str,
    *,
    leases: list[StreamLease] | None = None,
) -> bool:
    """True when this stream may proceed. ``off`` always allows (and touches nothing); ``warn`` logs and allows; ``enforce``
    needs a token that is genuine, unexpired, bound to exactly this call/party/org/provider, and with exclusive call ownership."""
    mode = effective_mode()
    if mode == "off":
        return True
    reason = "ok"
    try:
        expiry = int(expires_at or "")
    except ValueError:
        expiry = 0
    expected = (
        stream_token(call_uuid, caller, org_id, campaign_target_id, provider, expiry)
        if expiry
        else None
    )
    if not token or not expiry:
        reason = "no_token"
    elif expected is None or not _same(expected, token):
        reason = "bad_token"
    elif time.time() >= expiry:
        reason = "expired_token"
    else:
        try:
            # One owner per authenticated call, across tokens/providers/targets.
            # No lease timeout: a paused worker must never overlap a replacement.
            # Dropped sockets release after cleanup; crashed workers require an
            # operator to establish they are dead before clearing ownership.
            field = hashlib.sha256(
                json.dumps([org_id or "", call_uuid], separators=(",", ":")).encode()
            ).hexdigest()
            if not strict_state():
                lease = StreamLease(state_key("lease:" + field), "", secrets.token_hex(32), expiring=True)
                took = (
                    await get_redis_pool().set(
                        lease.key, lease.owner, nx=True, ex=max(60, int(settings.plivo_stream_lease_seconds))
                    )
                    if call_uuid
                    else None
                )
                reason = "ok" if took else ("active_stream" if call_uuid else "stream_store_error")
                if took and leases is not None:
                    leases.append(lease)
                raise _Claimed(reason)
            lease = StreamLease(state_key("streams"), field, secrets.token_hex(32))
            result = (
                int(
                    await get_redis_pool().eval(
                        STREAM_CLAIM,
                        1,
                        lease.key,
                        lease.field,
                        lease.owner,
                        max(1, int(settings.plivo_stream_capacity)),
                    )
                )
                if call_uuid
                else -1
            )
            reason = {
                1: "ok",
                0: "active_stream",
                -1: "stream_state_missing",
                -2: "stream_capacity",
            }.get(result, "stream_store_error")
            if result == 1 and leases is not None:
                leases.append(lease)
        except _Claimed as claimed:
            reason = str(claimed)
        except Exception:  # noqa: BLE001
            reason = "stream_store_error"
    if reason == "ok":
        return True
    if mode == "enforce":
        logger.warning("voice_stream_rejected", reason=reason)
        return False
    logger.warning("voice_stream_would_reject", reason=reason)
    return True
