"""Set up the isolated Pilot Org on the pilot database, with call recording explicitly OFF.

Pilot mode already treats an org with no policy as "do not record", but an explicit ``recording_mode: off`` is
better than relying on a default: it is visible in the dashboard and survives a change of environment.

``apply`` is idempotent and only ever touches the ONE org named by ``DEFAULT_ORG_ID``:

1. ``ensure_env_seed_data`` (the same function the API runs at startup) creates the org, the platform owner, the
   owner's membership and the org's phone-number rows from the environment, only where they are missing;
2. the seeded placeholder name "Demo Org" is renamed "Veerox Pilot" (a name an operator chose is never changed);
3. the org's compliance policy is set to ``{"recording_mode": "off"}``.

It refuses to run outside ``ENVIRONMENT=pilot``. It does not run migrations, create other orgs or users, contact a
telephony provider, or change a webhook. The caller (``scripts/pilot_setup_org.py``) adds the database-identity
checks and the explicit approval flag.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api import pilot
from apps.api.config import settings
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.startup_seed import ensure_env_seed_data

PILOT_ORG_NAME = "Veerox Pilot"
SEEDED_PLACEHOLDER_NAME = "Demo Org"
RECORDING_OFF = {"recording_mode": "off"}

PLAN = (
    "ensure the org DEFAULT_ORG_ID, the platform owner and its membership exist (only if missing)",
    "assign the Plivo/Twilio lines from the environment to that org (only if no org owns them)",
    f"rename the seeded placeholder '{SEEDED_PLACEHOLDER_NAME}' to '{PILOT_ORG_NAME}' (an operator-chosen name is never changed)",
    'set the org\'s compliance policy to {"recording_mode": "off"}',
)


async def apply(db: AsyncSession) -> dict[str, Any]:
    """Create/complete the pilot org. Raises ``RuntimeError`` outside pilot mode."""
    if not pilot.pilot_mode():
        raise RuntimeError("pilot setup only runs with ENVIRONMENT=pilot")
    org_id = UUID(settings.default_org_id)
    await ensure_env_seed_data(db)
    org = await db.get(Org, org_id)
    if org is None:  # ensure_env_seed_data commits; this would mean it failed silently
        raise RuntimeError("the pilot org was not created")
    renamed = org.name == SEEDED_PLACEHOLDER_NAME
    if renamed:
        org.name = PILOT_ORG_NAME
    org.voice_compliance = dict(RECORDING_OFF)
    await db.commit()
    return {"org_exists": True, "renamed": renamed, **await describe(db, org_id)}


async def describe(db: AsyncSession, org_id: UUID | None = None) -> dict[str, Any]:
    """Read-only summary of the pilot org (no names, numbers or personal data)."""
    org_id = org_id or UUID(settings.default_org_id)
    org = await db.get(Org, org_id)
    if org is None:
        return {"org_exists": False}
    lines = (
        await db.execute(select(OrgPhoneNumber.provider, func.count()).where(OrgPhoneNumber.org_id == org_id).group_by(OrgPhoneNumber.provider))
    ).all()
    return {
        "org_exists": True,
        "is_named_pilot": org.name == PILOT_ORG_NAME,
        "recording_mode": (org.voice_compliance or {}).get("recording_mode", "no policy"),
        "recording_explicitly_off": (org.voice_compliance or {}).get("recording_mode") == "off",
        "phone_number_rows": {provider: count for provider, count in lines},
    }
