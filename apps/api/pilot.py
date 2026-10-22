"""Guard rails for a controlled pilot against a disposable database.

Active ONLY when ``ENVIRONMENT=pilot``. Every other environment behaves exactly as before: nothing here is
consulted, and nothing is on by default.

In pilot mode:

* outbound calls, SMS, WhatsApp messages and e-mail are refused unless the destination is on an explicit
  allowlist (``PILOT_ALLOWED_NUMBERS`` / ``PILOT_ALLOWED_EMAILS``); an empty list blocks everything, so a
  forgotten setting fails closed instead of reaching a real customer;
* provider configuration changes (Plivo Answer URL, Meta webhook registration) are skipped unless
  ``PILOT_ALLOW_PROVIDER_CONFIG_CHANGES=true``, so saving a setting cannot repoint a real number;
* an org with no recording policy does not record (see ``compliance.resolve_recording_policy``);
* the background workers (campaign dialer, WhatsApp and follow-up dispatchers, billing/usage jobs, startup
  Plivo registration) do not start unless ``PILOT_WORKERS_ENABLED=true``;
* ``PILOT_DATABASE_NAME`` lets ``scripts/pilot_preflight.py`` confirm it is talking to the intended database.

A blocked send raises ``PilotBlocked``, an ``httpx.HTTPError``: every existing caller already treats that as
"the provider failed", so a refusal cannot crash a call or a worker.
"""

from __future__ import annotations

import re

import httpx
import structlog

from apps.api.config import settings

logger = structlog.get_logger(__name__)


class PilotBlocked(httpx.HTTPError):
    """A send that pilot mode refuses."""


def pilot_mode() -> bool:
    return (settings.environment or "").strip().lower() == "pilot"


def workers_enabled() -> bool:
    """Whether background workers should start: always outside pilot mode, opt-in inside it."""
    return (not pilot_mode()) or bool(settings.pilot_workers_enabled)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _allowed_numbers() -> set[str]:
    return {d[-10:] for d in (_digits(p) for p in settings.pilot_allowed_numbers.split(",")) if len(d) >= 7}


def _allowed_emails() -> set[str]:
    return {e.strip().lower() for e in settings.pilot_allowed_emails.split(",") if e.strip()}


def check_number_allowed(to: str, *, what: str) -> None:
    """Raise ``PilotBlocked`` in pilot mode unless ``to`` is an allowlisted number. No-op elsewhere."""
    if not pilot_mode():
        return
    if _digits(to)[-10:] not in _allowed_numbers():
        logger.warning("pilot_outbound_blocked", what=what, to=f"***{_digits(to)[-4:]}")
        raise PilotBlocked(f"pilot mode: {what} to a number that is not on PILOT_ALLOWED_NUMBERS")


_outbound_attempts = 0


def reserve_outbound_call(what: str = "call") -> None:
    """Pilot mode: count this outbound attempt and refuse it once ``PILOT_MAX_OUTBOUND_CALLS`` is reached.

    Counted per process, at the moment a call is about to be handed to a provider (an attempt that then fails at
    the provider still counts, because money may have been spent). Restarting the API resets it, so it is a guard
    against a mistake in one run, not an account-level budget. 0 (default) = no limit; no-op outside pilot mode."""
    global _outbound_attempts
    if not pilot_mode():
        return
    limit = int(settings.pilot_max_outbound_calls or 0)
    if limit and _outbound_attempts >= limit:
        logger.warning("pilot_outbound_limit_reached", what=what, limit=limit)
        raise PilotBlocked(f"pilot mode: the limit of {limit} outbound {what}(s) for this run has been reached")
    _outbound_attempts += 1


def reset_outbound_counter() -> None:
    """Tests, and an operator restarting a run on purpose."""
    global _outbound_attempts
    _outbound_attempts = 0


def provider_config_changes_blocked(what: str) -> bool:
    """True (and logged) in pilot mode unless ``PILOT_ALLOW_PROVIDER_CONFIG_CHANGES=true``.

    Registering an Answer URL at Plivo or a webhook at Meta changes where a real number's traffic goes. In a pilot
    that is a deliberate, approved step, never a side effect of saving a setting. Callers skip the change."""
    if pilot_mode() and not settings.pilot_allow_provider_config_changes:
        logger.warning("pilot_provider_config_change_skipped", what=what)
        return True
    return False


def check_email_allowed(to: str) -> None:
    if not pilot_mode():
        return
    if (to or "").strip().lower() not in _allowed_emails():
        logger.warning("pilot_email_blocked")
        raise PilotBlocked("pilot mode: e-mail to an address that is not on PILOT_ALLOWED_EMAILS")
