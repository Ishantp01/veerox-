"""Tests for the WhatsApp media-asset feature:

- admin CRUD (apps.api.routers.admin's /admin/whatsapp-assets)
- the public serve route (apps.api.routers.media)
- the send_whatsapp_file agent tool (apps.api.core.tools)
- wa_client.send_media payload shaping

Fixture setup mirrors test_script_endpoints.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core import tools
from apps.api.core.tools import send_whatsapp_file
from apps.api.db.models import Org, User, WhatsAppAsset
from apps.api.deps import get_db, get_redis_dep
from apps.api.tests.conftest import FakeRedis

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ADMIN_HEADERS = {"X-Admin-Token": settings.admin_token}


@pytest_asyncio.fixture
async def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: FakeRedis
) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.main import create_app

    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed_org(db: AsyncSession) -> None:
    db.add(Org(id=ORG_ID, name="Test Org"))
    await db.commit()


async def _upload(
    client: AsyncClient,
    *,
    name: str = "Price List",
    description: str | None = "Current pricing",
    filename: str = "prices.pdf",
    content: bytes = b"%PDF-1.4 fake",
    content_type: str = "application/pdf",
) -> dict:
    data = {"name": name}
    if description is not None:
        data["description"] = description
    response = await client.post(
        "/admin/whatsapp-assets",
        data=data,
        files={"file": (filename, content, content_type)},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# Admin CRUD
# --------------------------------------------------------------------------


async def test_list_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    response = await client.get("/admin/whatsapp-assets", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == []


async def test_upload_infers_media_type_and_hides_bytes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)

    pdf = await _upload(client, filename="p.pdf", content_type="application/pdf")
    img = await _upload(client, name="Brochure", filename="b.png", content_type="image/png")
    vid = await _upload(client, name="Demo", filename="d.mp4", content_type="video/mp4")

    assert pdf["media_type"] == "document"
    assert img["media_type"] == "image"
    assert vid["media_type"] == "video"
    # Metadata only — bytes / access_key never serialised.
    assert "data" not in pdf and "access_key" not in pdf
    assert pdf["size_bytes"] == len(b"%PDF-1.4 fake")


async def test_upload_rejects_oversized_file(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    monkeypatch.setattr("apps.api.routers.admin.MAX_ASSET_BYTES", 8)

    response = await client.post(
        "/admin/whatsapp-assets",
        data={"name": "Big"},
        files={"file": ("big.pdf", b"way too many bytes", "application/pdf")},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 413


async def test_patch_renames_and_edits_description(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    created = await _upload(client)

    response = await client.patch(
        f"/admin/whatsapp-assets/{created['id']}",
        json={"name": "Pricing 2026", "description": "Updated"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Pricing 2026"
    assert body["description"] == "Updated"
    assert body["media_type"] == "document"  # untouched


async def test_delete_removes_asset(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    created = await _upload(client)

    response = await client.delete(
        f"/admin/whatsapp-assets/{created['id']}", headers=ADMIN_HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert (await client.get("/admin/whatsapp-assets", headers=ADMIN_HEADERS)).json() == []


async def test_patch_delete_404_for_unknown(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(db_session)
    missing = uuid.uuid4()
    assert (
        await client.patch(
            f"/admin/whatsapp-assets/{missing}", json={"name": "x"}, headers=ADMIN_HEADERS
        )
    ).status_code == 404
    assert (
        await client.delete(f"/admin/whatsapp-assets/{missing}", headers=ADMIN_HEADERS)
    ).status_code == 404


# --------------------------------------------------------------------------
# Public serve route
# --------------------------------------------------------------------------


async def test_serve_route_returns_bytes_with_correct_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await _upload(client, content=b"%PDF-1.4 hello", content_type="application/pdf")

    asset = (await db_session.execute(_select_all_assets())).scalars().one()
    response = await client.get(f"/media/wa-asset/{asset.id}?k={asset.access_key}")

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 hello"
    assert response.headers["content-type"].startswith("application/pdf")


async def test_serve_route_404_on_bad_or_missing_key(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    await _upload(client)
    asset = (await db_session.execute(_select_all_assets())).scalars().one()

    assert (await client.get(f"/media/wa-asset/{asset.id}?k=wrong")).status_code == 404
    assert (await client.get(f"/media/wa-asset/{asset.id}")).status_code == 422  # k required
    assert (
        await client.get(f"/media/wa-asset/{uuid.uuid4()}?k=whatever")
    ).status_code == 404


def _select_all_assets():
    from sqlalchemy import select

    return select(WhatsAppAsset)


# --------------------------------------------------------------------------
# send_whatsapp_file tool
# --------------------------------------------------------------------------


@pytest.fixture
def capture_send_media(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def _fake_send_media(to, media_type, link, **kwargs):  # noqa: ANN001
        calls.append({"to": to, "media_type": media_type, "link": link, **kwargs})
        return {"messages": [{"id": "wamid.TEST"}]}

    monkeypatch.setattr(tools.wa_client, "send_media", _fake_send_media)
    return calls


async def _seed_asset(db: AsyncSession, **overrides) -> WhatsAppAsset:  # noqa: ANN003
    asset = WhatsAppAsset(
        org_id=ORG_ID,
        name=overrides.get("name", "Price List"),
        description=overrides.get("description"),
        media_type=overrides.get("media_type", "document"),
        filename=overrides.get("filename", "prices.pdf"),
        mime_type=overrides.get("mime_type", "application/pdf"),
        size_bytes=overrides.get("size_bytes", 12),
        data=overrides.get("data", b"%PDF-1.4 fake"),
        access_key=overrides.get("access_key", "testkey"),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def test_send_whatsapp_file_happy_path(
    db_session: AsyncSession, capture_send_media: list[dict]
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+919000000001")
    db_session.add(user)
    await db_session.commit()
    await _seed_asset(db_session)

    result = await send_whatsapp_file(
        db_session, name="price list", caption="Here you go", user_id=user.id, org_id=ORG_ID
    )

    assert result == {"status": "ok", "phone": "+919000000001", "file": "Price List"}
    assert len(capture_send_media) == 1
    call = capture_send_media[0]
    assert call["media_type"] == "document"
    assert call["caption"] == "Here you go"
    assert call["filename"] == "prices.pdf"
    assert "/media/wa-asset/" in call["link"] and "k=testkey" in call["link"]


async def test_send_whatsapp_file_no_match_returns_available(
    db_session: AsyncSession, capture_send_media: list[dict]
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+919000000002")
    db_session.add(user)
    await db_session.commit()
    await _seed_asset(db_session, name="Company Brochure")

    result = await send_whatsapp_file(
        db_session, name="tax return", user_id=user.id, org_id=ORG_ID
    )

    assert result["status"] == "error"
    assert result["reason"] == "no_matching_file"
    assert result["available"] == ["Company Brochure"]
    assert capture_send_media == []


async def test_send_whatsapp_file_outside_24h_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_org(db_session)
    user = User(org_id=ORG_ID, phone="+919000000003")
    db_session.add(user)
    await db_session.commit()
    await _seed_asset(db_session)

    async def _raise_reengagement(*args, **kwargs):  # noqa: ANN002, ANN003
        req = httpx.Request("POST", "https://graph.facebook.com/v21.0/x/messages")
        resp = httpx.Response(
            400, json={"error": {"code": 131047, "message": "re-engagement"}}, request=req
        )
        raise httpx.HTTPStatusError("400", request=req, response=resp)

    monkeypatch.setattr(tools.wa_client, "send_media", _raise_reengagement)

    result = await send_whatsapp_file(
        db_session, name="Price List", user_id=user.id, org_id=ORG_ID
    )
    assert result == {"status": "error", "reason": "outside_24h_window"}


# --------------------------------------------------------------------------
# wa_client.send_media
# --------------------------------------------------------------------------


async def test_send_media_builds_document_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"messages": [{"id": "wamid.X"}]}

    async def _fake_post(url, json, headers):  # noqa: ANN001
        sent["url"] = url
        sent["json"] = json
        return _Resp()

    monkeypatch.setattr(wa_client._http, "post", _fake_post)

    await wa_client.send_media(
        "+919999999999",
        "document",
        "https://example.com/f.pdf",
        caption="hi",
        filename="f.pdf",
    )

    assert sent["json"]["type"] == "document"
    assert sent["json"]["document"] == {
        "link": "https://example.com/f.pdf",
        "caption": "hi",
        "filename": "f.pdf",
    }


async def test_send_media_image_omits_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"messages": [{"id": "wamid.X"}]}

    async def _fake_post(url, json, headers):  # noqa: ANN001
        sent["json"] = json
        return _Resp()

    monkeypatch.setattr(wa_client._http, "post", _fake_post)

    await wa_client.send_media(
        "+919999999999", "image", "https://example.com/i.png", filename="i.png"
    )

    assert sent["json"]["type"] == "image"
    assert sent["json"]["image"] == {"link": "https://example.com/i.png"}
