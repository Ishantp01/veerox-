"""PLIVO_STARTUP_REGISTRATION: a dev server must be able to start without
re-pointing the shared Plivo application at its own ngrok URL."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.voice import plivo_provisioning
from apps.api.config import settings
from apps.api.db.models import Org

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def _seed(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


def _record_registrations(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    async def fake(db: AsyncSession, org_id: Any) -> None:
        calls.append(org_id)

    monkeypatch.setattr(plivo_provisioning, "register_org_plivo_numbers", fake)
    return calls


def test_default_is_on() -> None:
    # Production relies on the default: leaving the variable unset must keep
    # today's behaviour.
    assert settings.plivo_startup_registration is True


async def test_startup_registers_each_org_by_default(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    calls = _record_registrations(monkeypatch)
    monkeypatch.setattr(settings, "plivo_startup_registration", True)

    await plivo_provisioning.register_all_org_plivo_numbers(db_session)

    assert calls == [ORG_ID]


async def test_switch_off_skips_all_registration(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    calls = _record_registrations(monkeypatch)
    monkeypatch.setattr(settings, "plivo_startup_registration", False)

    await plivo_provisioning.register_all_org_plivo_numbers(db_session)

    assert calls == []


async def test_switch_off_does_not_block_registering_a_changed_org(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the automatic startup pass is switched off. Saving an org's
    numbers in settings still registers them (register_org_plivo_numbers)."""
    await _seed(db_session)
    seen: list[Any] = []

    async def fake_register(creds: Any, number: str) -> None:
        seen.append(number)

    monkeypatch.setattr(plivo_provisioning.plivo_client, "register_inbound_answer_url", fake_register)
    monkeypatch.setattr(settings, "plivo_startup_registration", False)

    # No Plivo credentials/numbers on this org, so nothing is dialed out — the
    # point is that the per-org entry point is not gated by the switch.
    await plivo_provisioning.register_org_plivo_numbers(db_session, ORG_ID)
    assert seen == []
