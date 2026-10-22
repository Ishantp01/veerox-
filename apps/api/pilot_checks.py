"""Pre-flight checks for a pilot environment file. Pure functions over two parsed env dicts - the pilot file
and the main ``.env`` - so they can be tested offline and never need a network.

The strongest check does not depend on knowing which hosts are production: the pilot database and Redis must
differ from the ones in the main ``.env`` (host, port AND database/instance), and the database name must be the
explicit ``PILOT_DATABASE_NAME`` and look like a pilot/test database. Secrets are compared, never printed:
results name the variable, not its value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Hosts or fragments that must never appear in a pilot file, whatever else is configured.
BUILTIN_FORBIDDEN_FRAGMENTS = ("veerox-db", "c3eoio6kg92s", "master.veerox-redis", "3.139.241.196", "workassignai.com")
# Credentials that, if identical to the main .env, let a pilot reach the same external account.
SHARED_CREDENTIAL_NAMES = (
    "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
    "META_ACCESS_TOKEN", "META_APP_SECRET", "RAZORPAY_KEY_SECRET", "BREVO_API_KEY", "SECRET_ENCRYPTION_KEY", "ADMIN_TOKEN",
    "SENTRY_DSN",
)
# Variables that would reach real customers or accounts if a pilot file carried them with real values.
RISKY_WHEN_SET = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "SENTRY_DSN", "META_APP_SECRET")


# Written into the pilot file by its owner, never by a tool, to run the pilot on the SAME database and Redis as the
# main .env. It turns "is a different instance" failures into loud warnings; it never relaxes the production-host
# check, the outbound allowlist, the worker switch or the privacy checks.
ACK_NAME = "PILOT_ACKNOWLEDGE_SHARED_DATA"
ACK_VALUE = "database-and-redis-hold-no-real-customer-data"


@dataclass(frozen=True)
class Result:
    status: str  # PASS | FAIL | WARN
    check: str
    detail: str


def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if match and not line.lstrip().startswith("#"):
            value = match.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            env[match.group(1)] = value
    return env


def _parts(url: str) -> tuple[str, int | None, str]:
    """(host, port, database-or-path) of a connection URL, lower-cased; ('', None, '') if unparsable."""
    try:
        u = urlparse(re.sub(r"\+\w+://", "://", url))
        return (u.hostname or "").lower(), u.port, (u.path or "").lstrip("/").lower()
    except ValueError:
        return "", None, ""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def check_pilot_env(pilot: dict[str, str], main: dict[str, str]) -> list[Result]:
    out: list[Result] = []
    shared_ok = pilot.get(ACK_NAME, "") == ACK_VALUE

    def add(ok: bool, check: str, detail: str, *, warn_only: bool = False, shared: bool = False) -> None:
        if shared and shared_ok and not ok:
            out.append(Result("WARN", check, "ACKNOWLEDGED shared data: " + detail))
            return
        out.append(Result("PASS" if ok else ("WARN" if warn_only else "FAIL"), check, detail))

    if shared_ok:
        out.append(Result("WARN", "pilot shares the main .env's data stores", f"{ACK_NAME} is set: you have said the database and Redis hold no real customer data. Redis keys such as the kill switch and handoff queue are global."))
    elif ACK_NAME in pilot:
        out.append(Result("FAIL", "shared-data acknowledgement is exact", f"{ACK_NAME} must be exactly '{ACK_VALUE}' (or removed)"))

    add(pilot.get("ENVIRONMENT", "").lower() == "pilot", "ENVIRONMENT is pilot", "ENVIRONMENT must be exactly 'pilot' (it switches on the outbound allowlist and stops the background workers)")

    # --- database
    db_url = pilot.get("DATABASE_URL", "")
    host, port, dbname = _parts(db_url)
    main_host, main_port, main_db = _parts(main.get("DATABASE_URL", ""))
    add(bool(host and dbname), "DATABASE_URL present", "DATABASE_URL must name a host and a database")
    add(not (host == main_host and port == main_port and dbname == main_db), "database differs from main .env", "host, port and database name are all identical to the main .env", shared=True)
    add(not (host and host == main_host and dbname == main_db), "database name differs on the same server", "same server AND same database name as the main .env", shared=True)
    expected = pilot.get("PILOT_DATABASE_NAME", "").lower()
    add(bool(expected) and dbname == expected, "database is the declared pilot database", "PILOT_DATABASE_NAME must be set and equal the database in DATABASE_URL")
    # A project-default name (neondb) in a DIFFERENT server/project is expected and the host already isolates it; on the
    # same server as the main .env a neutral name is not acceptable.
    add(bool(re.search(r"(pilot|test)", dbname)), "database name looks disposable", "database name should contain 'pilot' or 'test'", warn_only=bool(host and host != main_host), shared=True)
    forbidden = BUILTIN_FORBIDDEN_FRAGMENTS + tuple(f.strip().lower() for f in pilot.get("PILOT_FORBIDDEN_HOSTS", "").split(",") if f.strip())
    blob = " ".join([db_url, pilot.get("REDIS_URL", ""), pilot.get("PUBLIC_BASE_URL", "")]).lower()
    hit = [f for f in forbidden if f in blob]
    add(not hit, "no known production host", f"a known production fragment appears in DATABASE_URL/REDIS_URL/PUBLIC_BASE_URL ({len(hit)} match)")
    add(not db_url.lower().startswith("sqlite"), "database is PostgreSQL", "the pilot needs the same database engine as production", warn_only=True)

    # --- redis
    redis_url = pilot.get("REDIS_URL", "")
    rhost, rport, rdb = _parts(redis_url)
    mhost, mport, mdb = _parts(main.get("REDIS_URL", ""))
    add(bool(rhost), "REDIS_URL present", "REDIS_URL must name a host")
    add(not (rhost == mhost and rport == mport), "redis instance differs from main .env", "same Redis host and port as the main .env: key prefixes and database numbers do NOT isolate it (kill switch, handoff queue and sessions are global keys)", shared=True)
    add(rhost != mhost, "redis host differs from main .env", "same Redis host as the main .env", shared=True)

    # --- outbound safety
    add(bool(re.sub(r"[\s,]", "", pilot.get("PILOT_ALLOWED_NUMBERS", ""))), "outbound number allowlist", "PILOT_ALLOWED_NUMBERS is empty: every call, SMS and WhatsApp message will be refused (safe, but nothing can be tested)", warn_only=True)
    add(not _truthy(pilot.get("PILOT_WORKERS_ENABLED")), "background workers stay off", "PILOT_WORKERS_ENABLED=true starts the campaign dialer and dispatchers")
    add(not _truthy(pilot.get("PLIVO_STARTUP_REGISTRATION", "false")), "no startup Plivo registration", "PLIVO_STARTUP_REGISTRATION must be false so the pilot never repoints numbers")

    # --- paid-call safeguards (warnings: a pilot with no calls planned does not need them)
    add(int(re.sub(r"\D", "", pilot.get("PILOT_MAX_OUTBOUND_CALLS", "0")) or 0) > 0, "per-run outbound call limit", "PILOT_MAX_OUTBOUND_CALLS is unset: nothing stops a second call attempt in one run", warn_only=True)
    add(int(re.sub(r"\D", "", pilot.get("VOICE_OUTBOUND_TIME_LIMIT_SECONDS", "0")) or 0) > 0, "provider call-length limit", "VOICE_OUTBOUND_TIME_LIMIT_SECONDS is unset: the provider is not asked to end a call at a set length", warn_only=True)

    # --- webhooks
    base = pilot.get("PUBLIC_BASE_URL", "")
    add(base.startswith("https://") and "example.com" not in base, "PUBLIC_BASE_URL is an https tunnel", "PUBLIC_BASE_URL must be your pilot tunnel URL")
    add(base.rstrip("/") != main.get("PUBLIC_BASE_URL", "<unset>").rstrip("/"), "tunnel differs from main .env", "the same PUBLIC_BASE_URL as the main .env: the dev server and the pilot can't both sit behind one tunnel, and whichever is running receives the number's webhooks", warn_only=True, shared=True)

    # --- credentials shared with the main .env
    shared = [k for k in SHARED_CREDENTIAL_NAMES if pilot.get(k) and pilot.get(k) == main.get(k)]
    add(not shared, "no credential copied from main .env", "identical to the main .env: " + ", ".join(shared), warn_only=True)
    risky = [k for k in RISKY_WHEN_SET if pilot.get(k)]
    add(not risky, "billing/monitoring keys left out", "set in the pilot file (unneeded for a pilot): " + ", ".join(risky), warn_only=True)

    # --- privacy
    add(not _truthy(pilot.get("LOG_TRANSCRIPTS", "false")), "transcripts not logged", "LOG_TRANSCRIPTS should be false for the first pilot")
    add(pilot.get("LOG_PRIVACY_ENABLED", "true").lower() != "false", "log privacy on", "LOG_PRIVACY_ENABLED must not be false")

    # --- who may call the webhooks
    base_path = urlparse(pilot.get("PUBLIC_BASE_URL", "")).path.strip("/")
    add(not base_path, "PUBLIC_BASE_URL has no path", "signatures are checked against PUBLIC_BASE_URL + the request path; a path prefix breaks that unless the proxy keeps it")
    mode = pilot.get("PLIVO_SIGNATURE_MODE", "auto").strip().lower()
    add(mode in ("auto", "enforce"), "Plivo webhook signatures enforced", f"PLIVO_SIGNATURE_MODE={mode or '(empty)'}: forged requests to /voice/answer would be accepted", warn_only=True)

    # --- experimental voice flags are opt-in
    flags = [k for k in pilot if re.match(r"VOICE_[A-Z_]+$", k) and _truthy(pilot[k]) and k not in ("VOICE_LATENCY_TRACKING", "VOICE_LIVE_TURN_DETECTION", "VOICE_GREETING_AI_DISCLOSURE")]
    add(not flags, "experimental voice flags are off", "on in the pilot file: " + ", ".join(sorted(flags)) + " (enable one at a time, after its probe)", warn_only=True)
    return out


def summarize(results: list[Result]) -> tuple[int, int, int]:
    return (
        sum(r.status == "PASS" for r in results),
        sum(r.status == "WARN" for r in results),
        sum(r.status == "FAIL" for r in results),
    )
