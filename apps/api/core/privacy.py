"""Log hygiene: mask phone numbers and redact obviously sensitive values.

Applies to LOG LINES only. Nothing here changes what is stored in the database,
shown in the dashboard, spoken to a caller or sent to a provider. The goal is
that an operator reading logs (or a log vendor storing them) does not get
customers' phone numbers, one-time codes, card/ID numbers or e-mail addresses
for free, and that an org can stop what callers say being logged at all
(``LOG_TRANSCRIPTS=false``).

This is pattern-based redaction, so it is a best-effort reduction, not a
guarantee: a spoken ID number in an unusual format can still get through.
"""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from apps.api.config import settings

# Keys whose value is (or contains) a phone number: shown as ****1234.
MASK_KEYS = frozenset(
    {
        "caller",
        "phone",
        "to",
        "from",
        "to_phone",
        "from_phone",
        "phone_number",
        "recipient",
        "number",
        "msisdn",
    }
)
# Free-text keys scanned for sensitive patterns.
REDACT_KEYS = frozenset(
    {
        "text",
        "transcript",
        "error",
        "message",
        "reason",
        "body",
        "summary",
        "notes",
        "event",
        "exception",
        "stack",
        "close_reason",
    }
)
# Log events that carry what a caller or the agent said.
TRANSCRIPT_EVENTS = frozenset(
    {
        "voice_user_transcript",
        "voice_assistant_transcript",
        "voice_live_verdict",
        "voice_live_llm_verdict",
        "voice_endpoint_hold",
        "voice_backchannel_ignored",
        "voice_language_switch_unresolved",
    }
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# A one-time code / PIN spoken or typed after its keyword ("otp is 4829", "pin: 1234").
_CODE_RE = re.compile(r"(?i)\b(otp|pin|passcode|password|cvv|code)\b([^\d]{0,14})(\d{3,8})\b")
# 9+ digits, allowing separators between them: phone, Aadhaar, card, account numbers.
_LONG_NUMBER_RE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]?\d){8,}(?!\w)")
_URL_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+")
# Telephony REST paths carry the account id (Plivo ``/Account/<auth id>/``, Twilio ``/Accounts/<sid>/``): half of a
# credential pair, and it shows up in every HTTP client request line.
_ACCOUNT_PATH_RE = re.compile(r"(/Accounts?/)[A-Za-z0-9]{10,}")
_QUERY_RE = re.compile(r"([?&][^=&#\s\"']+)=([^&#\s\"']*)")
_AUTH_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[a-zA-Z0-9._~+/=-]+")
_TOKEN_RE = re.compile(r"\b(?:sk-(?:proj-|svcacct-)?[a-zA-Z0-9_-]{8,}|xox[baprs]-[a-zA-Z0-9-]+)\b")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|secret|authorization|xi[_-]?api[_-]?key)\b"
    r"([\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_SECRET_KEYS = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "apikey",
        "xiapikey",
        "xapikey",
        "xadmintoken",
        "authtoken",
        "clientsecret",
        "admintoken",
        "privatekey",
        "secretencryptionkey",
        "databaseurl",
        "redisurl",
        "connectionstring",
        "dsn",
        "credentials",
        "cookie",
        "setcookie",
        "sig",
        "signature",
        "nonce",
        "streamtoken",
        "xplivosignaturev3",
        "xplivosignaturemav3",
        "xplivosignaturev3nonce",
        "xtwiliosignature",
    }
)
_PAYLOAD_KEYS = frozenset(
    {
        "body",
        "payload",
        "params",
        "headers",
        "request",
        "response",
        "requestbody",
        "responsebody",
        "providerpayload",
        "raw",
        "rawmessage",
        "arguments",
        "content",
    }
)
_SPEECH_KEYS = frozenset({"transcript", "usertext", "assistanttext", "utterancetext"})
_ERROR_FIELDS = frozenset({"code", "type", "status", "status_code", "retryable"})


def _key_name(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _without_userinfo(match: re.Match[str]) -> str:
    try:
        parts = urlsplit(match.group(0))
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc.rsplit("@", 1)[-1],
                parts.path,
                parts.query,
                "[redacted]" if parts.fragment else "",
            )
        )
    except ValueError:
        return "[url withheld]"


def mask_phone(value: Any) -> str:
    """``+919876543210`` -> ``+********3210``. Keeps the last four digits so
    two log lines about the same caller can still be correlated."""
    raw = str(value)
    if re.fullmatch(r"\+?\*+\d{0,4}", raw):
        return raw  # the final formatter also applies this processor
    digits = re.sub(r"\D", "", raw)
    if len(digits) <= 4:
        return "***"
    return ("+" if raw.strip().startswith("+") else "") + "*" * (len(digits) - 4) + digits[-4:]


def redact_text(text: str) -> str:
    """Replace e-mails, ID/card/phone-length numbers, PANs and spoken one-time codes."""
    if not text:
        return text
    text = _URL_RE.sub(_without_userinfo, text)
    text = _ACCOUNT_PATH_RE.sub(lambda m: m.group(1) + "[account]", text)
    text = _QUERY_RE.sub(lambda m: m.group(1) + "=[redacted]", text)
    text = _AUTH_RE.sub(lambda m: m.group(1) + " [redacted]", text)
    text = _SECRET_ASSIGN_RE.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
    text = _TOKEN_RE.sub("[credential]", text)
    text = re.sub(r"(?i)%2b(?=\d)", "+", text)
    text = _EMAIL_RE.sub("[email]", text)
    text = _PAN_RE.sub("[id]", text)
    text = _CODE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[code]", text)
    return _LONG_NUMBER_RE.sub(lambda m: mask_phone(m.group(0)), text)


def _sanitize(value: Any, key: str, *, depth: int = 0) -> Any:
    """Bound traversal and fail closed for arbitrary provider/user objects."""
    name = _key_name(key)
    if name in _SECRET_KEYS or name.endswith(("password", "apikey", "authtoken", "clientsecret")):
        return "[redacted]"
    if name in _PAYLOAD_KEYS:
        return "[payload withheld]"
    if not settings.log_transcripts and (name in _SPEECH_KEYS or (depth and name == "text")):
        return "[speech withheld]"
    if depth >= 8:
        return "[depth limit]"
    if isinstance(value, dict):
        # Provider errors are data, not operational log schemas. Keep only
        # known scalar metadata; messages/details can echo an entire request.
        if name == "error":
            return {
                k: _sanitize(v, k, depth=depth + 1)
                for k, v in value.items()
                if k in _ERROR_FIELDS and isinstance(v, (str, int, float, bool, type(None)))
            }
        return {
            redact_text(k): _sanitize(v, k, depth=depth + 1)
            for k, v in list(value.items())[:100]
            if isinstance(k, str)
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(v, key, depth=depth + 1) for v in value[:100]]
    if name in {_key_name(k) for k in MASK_KEYS} and isinstance(value, (str, int)):
        if not isinstance(value, bool) and value != "unknown":
            return mask_phone(value)
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    # Logging must never invoke an untrusted object's __str__/__repr__.
    return "[object withheld]"


def privacy_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: see the module docstring. Never raises."""
    try:
        if not settings.log_transcripts and event_dict.get("event") in TRANSCRIPT_EVENTS:
            for key in ("text", "transcript"):
                value = event_dict.get(key)
                if isinstance(value, str) and not re.fullmatch(r"<\d+ chars not logged>", value):
                    event_dict[key] = f"<{len(value)} chars not logged>"
        if not settings.log_privacy_enabled:
            return event_dict
        for key, value in list(event_dict.items()):
            # structlog needs these until exception formatting. The final
            # formatter pass sanitizes the generated exception/stack text.
            if key in {"exc_info", "stack_info", "_record", "_from_structlog"}:
                continue
            event_dict[key] = _sanitize(value, key)
    except Exception:  # noqa: BLE001 - a logging helper must never break the caller
        event_dict.clear()
        event_dict["event"] = "log_redaction_failed"
    return event_dict
