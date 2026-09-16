"""Tests for the WhatsApp webhook handlers (verification + receipt).

Each org configures its OWN Meta WhatsApp App (see
apps/api/core/org_credentials.py) — there is no platform-wide
settings.meta_verify_token/meta_app_secret any more. The GET handshake
accepts a match against ANY org's own verify token; the POST signature
check resolves which org owns the `phone_number_id` named in the (already
parsed) body and verifies against THAT org's own app_secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.crypto import encrypt_secret
from apps.api.db.models.org import Org
from apps.api.db.models.org_phone_number import OrgPhoneNumber

ORG_A_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b2")

ORG_A_VERIFY_TOKEN = "org-a-verify-token"
ORG_A_APP_SECRET = "org-a-app-secret"  # noqa: S105 — test fixture
ORG_A_PHONE_NUMBER_ID = "111000111"

ORG_B_VERIFY_TOKEN = "org-b-verify-token"
ORG_B_APP_SECRET = "org-b-app-secret"  # noqa: S105 — test fixture
ORG_B_PHONE_NUMBER_ID = "222000222"


async def _seed_org(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    verify_token: str | None,
    app_secret: str | None,
    phone_number_id: str | None,
) -> Org:
    org = Org(
        id=org_id,
        name=f"Org {org_id}",
        meta_verify_token_encrypted=encrypt_secret(verify_token) if verify_token else None,
        meta_app_secret_encrypted=encrypt_secret(app_secret) if app_secret else None,
    )
    db.add(org)
    await db.flush()
    if phone_number_id:
        db.add(OrgPhoneNumber(org_id=org_id, provider="whatsapp", phone_number=phone_number_id))
    await db.commit()
    return org


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _body_for(phone_number_id: str) -> bytes:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {"value": {"metadata": {"phone_number_id": phone_number_id}}},
                ]
            }
        ],
    }
    return json.dumps(payload).encode()


@pytest.mark.asyncio
async def test_verify_returns_challenge_on_token_match(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )

    response = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": ORG_A_VERIFY_TOKEN,
            "hub.challenge": "challenge-12345",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-12345"


@pytest.mark.asyncio
async def test_verify_accepts_second_orgs_token_even_when_first_org_also_configured(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """The GET handshake has no other signal than the token itself — it must
    match against ANY org's own verify token, not just the first one found."""
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )
    await _seed_org(
        db_session,
        org_id=ORG_B_ID,
        verify_token=ORG_B_VERIFY_TOKEN,
        app_secret=ORG_B_APP_SECRET,
        phone_number_id=ORG_B_PHONE_NUMBER_ID,
    )

    response = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": ORG_B_VERIFY_TOKEN,
            "hub.challenge": "challenge-67890",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-67890"


@pytest.mark.asyncio
async def test_verify_rejects_wrong_token(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )

    response = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "WRONG",
            "hub.challenge": "challenge-12345",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_verify_rejects_wrong_mode(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )

    response = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": ORG_A_VERIFY_TOKEN,
            "hub.challenge": "challenge-12345",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_post_rejects_missing_signature(client: AsyncClient) -> None:
    response = await client.post(
        "/webhook/whatsapp",
        content=b'{"object":"whatsapp_business_account"}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_rejects_bad_signature(client: AsyncClient, db_session: AsyncSession) -> None:
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )
    body = _body_for(ORG_A_PHONE_NUMBER_ID)
    bad_sig = _sign(body, secret="not-the-real-secret")  # noqa: S106
    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": bad_sig,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_rejects_unresolvable_phone_number_id(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """A phone_number_id that doesn't belong to any org can't be verified
    against anything — must reject rather than silently accept."""
    body = _body_for("999999999")
    sig = _sign(body, secret="whatever")
    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": sig,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_rejects_org_bs_signature_for_org_as_phone_number(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Org B's app_secret must never verify a webhook claiming to be for
    org A's phone_number_id — each org's secret only covers its own
    number(s)."""
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )
    await _seed_org(
        db_session,
        org_id=ORG_B_ID,
        verify_token=ORG_B_VERIFY_TOKEN,
        app_secret=ORG_B_APP_SECRET,
        phone_number_id=ORG_B_PHONE_NUMBER_ID,
    )

    body = _body_for(ORG_A_PHONE_NUMBER_ID)
    forged_sig = _sign(body, secret=ORG_B_APP_SECRET)

    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": forged_sig,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_accepts_good_signature_and_returns_fast(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler must ACK 200 without awaiting the LLM (Meta's <15s rule)."""
    await _seed_org(
        db_session,
        org_id=ORG_A_ID,
        verify_token=ORG_A_VERIFY_TOKEN,
        app_secret=ORG_A_APP_SECRET,
        phone_number_id=ORG_A_PHONE_NUMBER_ID,
    )

    # Patch process_inbound on the *webhook* module — that's the binding
    # FastAPI will hand to BackgroundTasks.
    from apps.api.channels.whatsapp import webhook as webhook_module

    captured: dict[str, Any] = {}

    async def fake_process_inbound(payload: dict[str, Any]) -> None:
        captured["payload"] = payload

    monkeypatch.setattr(webhook_module, "process_inbound", fake_process_inbound)

    body = _body_for(ORG_A_PHONE_NUMBER_ID)
    sig = _sign(body, secret=ORG_A_APP_SECRET)

    response = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": sig,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # BackgroundTasks run after the response is sent; httpx awaits the full
    # lifecycle including the background, so by the time we assert here the
    # task has executed.
    assert captured.get("payload") == json.loads(body)
