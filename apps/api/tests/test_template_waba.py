"""Templates belong to a WABA, and a WhatsApp number belongs to a WABA: listing and syncing are scoped to the chosen number's
WABA, a WABA with no templates shows none, and a campaign cannot mix a number with another WABA's template."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core.crypto import encrypt_secret
from apps.api.core.template_scope import ensure_template_matches_number
from apps.api.db.models import Org, OrgPhoneNumber, WhatsAppTemplate
from apps.api.routers import templates as templates_router

ADMIN = {"X-Admin-Token": settings.admin_token}
ORG_ID = uuid.UUID(settings.default_org_id)
DEFAULT_WABA = "1779011260088577"  # the org's default (Dignity)
OTHER_WABA = "2101869633891281"  # a second WABA on the same org (MVDK)


async def _seed(db: AsyncSession, *, default_waba: str | None = DEFAULT_WABA) -> tuple[OrgPhoneNumber, OrgPhoneNumber]:
    db.add(
        Org(
            id=ORG_ID,
            name="Dignity",
            meta_app_id="app-1",
            meta_app_secret_encrypted=encrypt_secret("secret"),
            meta_access_token_encrypted=encrypt_secret("token"),
            meta_whatsapp_business_account_id=default_waba,
        )
    )
    await db.flush()
    plain = OrgPhoneNumber(org_id=ORG_ID, provider="whatsapp", phone_number="1391376220715403", is_default=True, position=0)
    other = OrgPhoneNumber(
        org_id=ORG_ID, provider="whatsapp", phone_number="999902163204162", waba_id=OTHER_WABA, is_default=False, position=1
    )
    db.add_all([plain, other])
    db.add_all(
        [
            WhatsAppTemplate(org_id=ORG_ID, name="hello", language="en", waba_id=None, active=True),  # legacy row = default WABA
            WhatsAppTemplate(org_id=ORG_ID, name="promo_mvdk", language="en", waba_id=OTHER_WABA, active=True),
        ]
    )
    await db.commit()
    await db.refresh(plain)
    await db.refresh(other)
    return plain, other


def _meta(*names: str) -> list[dict[str, Any]]:
    return [
        {"name": n, "language": "en", "status": "APPROVED", "category": "UTILITY",
         "components": [{"type": "BODY", "text": f"{n} {{{{1}}}}"}]}
        for n in names
    ]


@pytest.fixture
def meta_by_waba(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[dict[str, Any]]]:
    """What Meta 'has', per WABA. Records which WABAs were asked for."""
    data: dict[str, list[dict[str, Any]]] = {}
    asked: list[str] = []

    async def fake_list_templates(access_token: str, business_account_id: str) -> list[dict[str, Any]]:
        asked.append(business_account_id)
        return data.get(business_account_id, [])

    monkeypatch.setattr(wa_client, "list_templates", fake_list_templates)
    data["_asked"] = asked  # type: ignore[assignment]
    return data


async def _names(client: AsyncClient, **params: Any) -> list[str]:
    resp = await client.get("/whatsapp-templates", headers=ADMIN, params=params)
    assert resp.status_code == 200, resp.text
    return sorted(t["name"] for t in resp.json())


async def test_list_is_scoped_to_the_chosen_numbers_waba(
    client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict
) -> None:
    plain, other = await _seed(db_session)
    assert await _names(client) == ["hello", "promo_mvdk"]  # no number chosen: unchanged behaviour
    assert await _names(client, number_id=str(plain.id)) == ["hello"]  # NULL waba on the number = default WABA
    assert await _names(client, number_id=str(other.id)) == ["promo_mvdk"]
    assert await _names(client, waba_id=OTHER_WABA) == ["promo_mvdk"]
    assert await _names(client, waba_id="5555") == []  # a WABA with nothing on it shows none


async def test_unknown_or_foreign_number_is_refused(client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict) -> None:
    await _seed(db_session)
    resp = await client.get("/whatsapp-templates", headers=ADMIN, params={"number_id": str(uuid.uuid4())})
    assert resp.status_code == 404


async def test_number_without_any_waba_is_refused(client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict) -> None:
    plain, _ = await _seed(db_session, default_waba=None)
    resp = await client.get("/whatsapp-templates", headers=ADMIN, params={"number_id": str(plain.id)})
    assert resp.status_code == 400


async def test_sync_reads_the_chosen_wabas_templates_and_stamps_them(
    client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict
) -> None:
    _, other = await _seed(db_session)
    meta_by_waba[OTHER_WABA] = _meta("promo_mvdk", "fresh_mvdk")

    resp = await client.post("/whatsapp-templates/sync", headers=ADMIN, params={"number_id": str(other.id)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert meta_by_waba["_asked"] == [OTHER_WABA]  # asked Meta for the number's WABA, not the default one
    assert body["waba_id"] == OTHER_WABA
    assert [t["name"] for t in body["created"]] == ["fresh_mvdk"]
    assert body["deactivated"] == 0
    assert await _names(client, waba_id=OTHER_WABA) == ["fresh_mvdk", "promo_mvdk"]
    assert await _names(client, waba_id=DEFAULT_WABA) == ["hello"]  # the other WABA is untouched


async def test_sync_of_a_waba_with_no_templates_shows_none(
    client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict
) -> None:
    _, other = await _seed(db_session)
    meta_by_waba[OTHER_WABA] = []  # Meta has nothing on this WABA

    resp = await client.post("/whatsapp-templates/sync", headers=ADMIN, params={"number_id": str(other.id)})
    assert resp.status_code == 200
    assert resp.json()["deactivated"] == 1
    assert await _names(client, number_id=str(other.id), active="true") == []
    assert await _names(client, waba_id=DEFAULT_WABA, active="true") == ["hello"]  # the default WABA is not affected
    # Nothing was deleted: the row is only switched off.
    assert await _names(client, waba_id=OTHER_WABA) == ["promo_mvdk"]


async def test_sync_does_not_switch_templates_off_when_metas_list_may_be_incomplete(
    client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, other = await _seed(db_session)
    monkeypatch.setattr(templates_router, "_META_LIST_PAGE", 1)
    meta_by_waba[OTHER_WABA] = _meta("some_other_one")  # a full page: the list may continue on a page we did not read

    resp = await client.post("/whatsapp-templates/sync", headers=ADMIN, params={"number_id": str(other.id)})
    assert resp.status_code == 200
    assert resp.json()["deactivated"] == 0
    assert await _names(client, waba_id=OTHER_WABA, active="true") == ["promo_mvdk", "some_other_one"]


async def test_sync_without_a_number_still_uses_the_default_waba(
    client: AsyncClient, db_session: AsyncSession, meta_by_waba: dict
) -> None:
    await _seed(db_session)
    meta_by_waba[DEFAULT_WABA] = _meta("hello", "added")
    resp = await client.post("/whatsapp-templates/sync", headers=ADMIN)
    assert resp.status_code == 200
    assert meta_by_waba["_asked"] == [DEFAULT_WABA]
    assert [t["name"] for t in resp.json()["created"]] == ["added"]
    assert await _names(client) == ["added", "hello", "promo_mvdk"]


async def test_campaign_check_rejects_a_template_from_another_waba(db_session: AsyncSession) -> None:
    plain, other = await _seed(db_session)
    with pytest.raises(HTTPException) as err:
        await ensure_template_matches_number(db_session, ORG_ID, other, "hello")  # default-WABA template on the MVDK number
    assert err.value.status_code == 400
    with pytest.raises(HTTPException):
        await ensure_template_matches_number(db_session, ORG_ID, plain, "promo_mvdk")  # MVDK template on the default number
    # The matching pairs, an unknown template, and no template at all are all fine.
    await ensure_template_matches_number(db_session, ORG_ID, plain, "hello")
    await ensure_template_matches_number(db_session, ORG_ID, other, "promo_mvdk")
    await ensure_template_matches_number(db_session, ORG_ID, other, "not_saved_locally")
    await ensure_template_matches_number(db_session, ORG_ID, other, None)


async def test_a_number_keeps_the_waba_it_was_saved_with(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(Org(id=ORG_ID, name="Dignity"))
    await db_session.commit()
    resp = await client.put(
        "/admin/org-numbers",
        headers=ADMIN,
        json={
            "phone_numbers": [
                {"provider": "whatsapp", "phone_number": "1391376220715403", "is_default": True},
                {"provider": "whatsapp", "phone_number": "999902163204162", "waba_id": " 2101869633891281 "},
                {"provider": "plivo", "phone_number": "+14155551234", "waba_id": "999"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    by_number = {n["phone_number"]: n for n in resp.json()["phone_numbers"]}
    assert by_number["1391376220715403"]["waba_id"] is None
    assert by_number["999902163204162"]["waba_id"] == OTHER_WABA
    assert by_number["14155551234"]["waba_id"] is None  # only WhatsApp numbers carry a WABA
