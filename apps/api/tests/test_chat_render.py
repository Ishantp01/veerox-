"""Chat shows the message the customer actually received, not a template marker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import settings
from apps.api.core.whatsapp_render import render_template_message
from apps.api.db.models import Conversation, Message, User, WhatsAppTemplate
from apps.api.tests.test_admin_endpoints import (  # noqa: F401  (fixtures)
    ADMIN_HEADERS,
    ORG_ID,
    _seed_org,
    client,
    fake_redis,
)

pytestmark = pytest.mark.asyncio

REMINDER = dict(
    name="appointment_reminder",
    language="en_US",
    param_labels=["name", "date", "time"],
    body_preview="Hi {{1}}, reminder: your appointment is on {{2}} at {{3}}.",
    header_type="TEXT",
    header_text="Appointment reminder",
    footer_text="Reply STOP to opt out",
    buttons=[{"type": "QUICK_REPLY", "text": "Confirm"}, {"type": "QUICK_REPLY", "text": "Reschedule"}],
)


def test_render_fills_params_and_includes_header_footer_buttons() -> None:
    template = WhatsAppTemplate(org_id=ORG_ID, **REMINDER)
    text = render_template_message(template, "appointment_reminder", ["Asha", "20 Nov", "4:00 PM"])
    assert text == (
        "Appointment reminder\n\n"
        "Hi Asha, reminder: your appointment is on 20 Nov at 4:00 PM.\n\n"
        "Reply STOP to opt out\n\n"
        "[ Confirm ]\n[ Reschedule ]"
    )


def test_render_leaves_unknown_placeholders_and_handles_missing_template() -> None:
    template = WhatsAppTemplate(org_id=ORG_ID, **{**REMINDER, "header_type": None, "footer_text": None, "buttons": []})
    assert render_template_message(template, "x", ["Asha"]) == (
        "Hi Asha, reminder: your appointment is on [date] at [time]."
    )
    assert render_template_message(None, "mystery") == "[Template: mystery]"


async def test_chat_resolves_legacy_template_marker_rows(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)
    db_session.add(WhatsAppTemplate(org_id=ORG_ID, **REMINDER))
    user = User(org_id=ORG_ID, phone="+918000000001", name="Asha")
    db_session.add(user)
    await db_session.flush()
    conv = Conversation(org_id=ORG_ID, user_id=user.id, channel="whatsapp")
    db_session.add(conv)
    await db_session.flush()
    db_session.add(
        Message(
            org_id=ORG_ID, conversation_id=conv.id, user_id=user.id, role="assistant",
            content="[template:appointment_reminder]", channel="whatsapp",
            created_at=datetime(2026, 11, 19, 6, 17, tzinfo=UTC),
        )
    )
    db_session.add(
        Message(
            org_id=ORG_ID, conversation_id=conv.id, user_id=user.id, role="assistant",
            content="[template:deleted_template]", channel="whatsapp",
            created_at=datetime(2026, 11, 19, 7, 0, tzinfo=UTC),
        )
    )
    await db_session.commit()

    headers = {"X-Admin-Token": settings.admin_token}
    thread = (await client.get(f"/admin/chat/leads/{user.id}", headers=headers)).json()
    first, second = (m["content"] for m in thread["messages"])
    assert first.startswith("Appointment reminder\n\nHi [name], reminder")
    assert "[ Confirm ]" in first
    assert second == "[template:deleted_template]"  # no saved template: shown as-is, not hidden

    listing = (await client.get("/admin/chat/leads", headers=headers)).json()
    assert listing[0]["last_message_preview"] == "[template:deleted_template]"


async def test_dashboard_template_send_is_stored_as_the_real_message(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_org(db_session)  # no Meta credentials -> endpoint records, returns "queued"
    db_session.add(WhatsAppTemplate(org_id=ORG_ID, **REMINDER))
    await db_session.commit()

    response = await client.post(
        "/admin/outbound/whatsapp",
        headers=ADMIN_HEADERS,
        json={
            "phone": "+918000000002",
            "template_name": "appointment_reminder",
            "template_params": ["Ravi", "21 Nov", "11:30 AM"],
        },
    )
    assert response.status_code == 200, response.text
    stored = (await db_session.execute(select(Message.content))).scalars().all()
    assert len(stored) == 1
    assert "Hi Ravi, reminder: your appointment is on 21 Nov at 11:30 AM." in stored[0]
    assert "[template:" not in stored[0]
