from __future__ import annotations

import uuid
from types import TracebackType

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.security import hash_token
from apps.api.db.models import AccountUser, Org, OrgMembership
from apps.api.db.models.org_phone_number import OrgPhoneNumber
from apps.api.db.startup_seed import (
    DEFAULT_OWNER_ID,
    ensure_env_seed_data,
    ensure_env_seed_data_on_startup,
)


@pytest.mark.asyncio
async def test_startup_seed_creates_default_org_owner_and_env_numbers(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr("apps.api.db.startup_seed.settings.default_org_id", str(org_id))
    monkeypatch.setattr("apps.api.db.startup_seed.settings.admin_token", "startup-token")
    monkeypatch.setattr("apps.api.db.startup_seed.settings.meta_phone_number_id", "wa-phone-id")
    monkeypatch.setattr("apps.api.db.startup_seed.settings.plivo_phone_number", "+91 2269986006")
    monkeypatch.setattr(
        "apps.api.db.startup_seed.settings.twilio_phone_number",
        "+1 (980) 372-2373",
    )

    await ensure_env_seed_data(db_session)

    org = await db_session.get(Org, org_id)
    assert org is not None
    assert org.whatsapp_phone_number_id == "wa-phone-id"

    owner = await db_session.get(AccountUser, DEFAULT_OWNER_ID)
    assert owner is not None
    assert owner.token_hash == hash_token("startup-token")
    assert owner.is_active is True
    assert owner.is_superuser is True

    membership = (
        await db_session.execute(
            select(OrgMembership).where(
                OrgMembership.org_id == org_id,
                OrgMembership.account_user_id == DEFAULT_OWNER_ID,
            )
        )
    ).scalar_one_or_none()
    assert membership is not None
    assert membership.role == "admin"

    rows = (
        await db_session.execute(
            select(OrgPhoneNumber.provider, OrgPhoneNumber.phone_number, OrgPhoneNumber.is_default)
            .where(OrgPhoneNumber.org_id == org_id)
            .order_by(OrgPhoneNumber.provider)
        )
    ).all()
    assert rows == [("plivo", "912269986006", True), ("twilio", "19803722373", True)]


@pytest.mark.asyncio
async def test_startup_seed_does_not_overwrite_existing_org_settings(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    db_session.add(Org(id=org_id, name="Existing", whatsapp_phone_number_id="existing-wa"))
    await db_session.commit()

    monkeypatch.setattr("apps.api.db.startup_seed.settings.default_org_id", str(org_id))
    monkeypatch.setattr("apps.api.db.startup_seed.settings.admin_token", "startup-token")
    monkeypatch.setattr("apps.api.db.startup_seed.settings.meta_phone_number_id", "env-wa")
    monkeypatch.setattr("apps.api.db.startup_seed.settings.plivo_phone_number", None)
    monkeypatch.setattr("apps.api.db.startup_seed.settings.twilio_phone_number", None)

    await ensure_env_seed_data(db_session)

    org = await db_session.get(Org, org_id)
    assert org is not None
    assert org.whatsapp_phone_number_id == "existing-wa"


class _BrokenStartupSession:
    async def __aenter__(self) -> None:
        raise RuntimeError("db unavailable")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


@pytest.mark.asyncio
async def test_startup_seed_on_startup_logs_and_continues_when_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.api.db.startup_seed.settings.startup_seed_required", False)
    monkeypatch.setattr(
        "apps.api.db.startup_seed.AsyncSessionLocal",
        lambda: _BrokenStartupSession(),
    )

    await ensure_env_seed_data_on_startup()


@pytest.mark.asyncio
async def test_startup_seed_on_startup_reraises_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("apps.api.db.startup_seed.settings.startup_seed_required", True)
    monkeypatch.setattr(
        "apps.api.db.startup_seed.AsyncSessionLocal",
        lambda: _BrokenStartupSession(),
    )

    with pytest.raises(RuntimeError, match="db unavailable"):
        await ensure_env_seed_data_on_startup()
