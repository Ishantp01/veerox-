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

from apps.api.config import settings

# Keys whose value is (or contains) a phone number: shown as ****1234.
MASK_KEYS = frozenset(
    {"caller", "phone", "to", "from", "to_phone", "from_phone", "phone_number", "recipient", "number", "msisdn"}
)
# Free-text keys scanned for sensitive patterns.
REDACT_KEYS = frozenset({"text", "transcript", "error", "message", "reason", "body", "summary", "notes"})
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


def mask_phone(value: Any) -> str:
    """``+919876543210`` -> ``+********3210``. Keeps the last four digits so
    two log lines about the same caller can still be correlated."""
    raw = str(value)
    digits = re.sub(r"\D", "", raw)
    if len(digits) <= 4:
        return "***"
    return ("+" if raw.strip().startswith("+") else "") + "*" * (len(digits) - 4) + digits[-4:]


def redact_text(text: str) -> str:
    """Replace e-mails, ID/card/phone-length numbers, PANs and spoken one-time codes."""
    if not text:
        return text
    text = _EMAIL_RE.sub("[email]", text)
    text = _PAN_RE.sub("[id]", text)
    text = _CODE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[code]", text)
    return _LONG_NUMBER_RE.sub(lambda m: mask_phone(m.group(0)), text)


def privacy_processor(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: see the module docstring. Never raises."""
    try:
        if not settings.log_transcripts and event_dict.get("event") in TRANSCRIPT_EVENTS:
            for key in ("text", "transcript"):
                value = event_dict.get(key)
                if isinstance(value, str):
                    event_dict[key] = f"<{len(value)} chars not logged>"
        if not settings.log_privacy_enabled:
            return event_dict
        for key in MASK_KEYS:
            value = event_dict.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "unknown":
                event_dict[key] = mask_phone(value)
        for key in REDACT_KEYS:
            value = event_dict.get(key)
            if isinstance(value, str):
                event_dict[key] = redact_text(value)
    except Exception:  # noqa: BLE001 - a logging helper must never break the caller
        return event_dict
    return event_dict
