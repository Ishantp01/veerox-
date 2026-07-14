from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT, VOICE_APPEND, WHATSAPP_APPEND
from apps.api.core.tools import TOOL_DEFINITIONS
from apps.api.db.models import Conversation, Lead, Message, User
from apps.api.deps import DbDep, RedisDep
from apps.api.rate_limit import limiter
from apps.api.schemas.admin import (
    KillSwitchIn,
    KillSwitchOut,
    OutboundCallIn,
    OutboundCallOut,
    OutboundWhatsappIn,
    OutboundWhatsappOut,
    PromptsOut,
)
from apps.api.schemas.conversation import ConversationOut, MessageOut
from apps.api.schemas.lead import LeadOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# Redis keys / channels used by the control plane.
KILL_SWITCH_KEY = "veerox:kill_switch"
HUMAN_HANDOFF_QUEUE = "human_handoff_queue"
ERROR_COUNTER_KEY_FMT = "veerox:errors:{date}"  # date = YYYY-MM-DD UTC

# Cost constants used by usd_spend_today. These mirror the planned values from
# implementation-plan.md §5.6 and intentionally live here rather than in
# apps/api/core/costs.py because that file is owned by worker 1 and may not
# exist yet. Once core/costs.py lands these can be re-imported from there.
_INPUT_USD_PER_TOKEN = 2.50 / 1_000_000  # $2.50 / 1M input tokens
_OUTPUT_USD_PER_TOKEN = 10.00 / 1_000_000  # $10.00 / 1M output tokens
_REALTIME_AUDIO_USD_PER_SECOND = 0.30 / 60.0  # $0.30 / minute of realtime audio


def _verify_admin(x_admin_token: str | None) -> None:
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


def _today_start_utc() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/stats")
async def get_stats(
    db: DbDep,
    redis: RedisDep,
    x_admin_token: str | None = Header(None),
) -> dict:
    _verify_admin(x_admin_token)

    today_start = _today_start_utc()

    users_today_result = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    )
    users_today = users_today_result.scalar_one()

    calls_today_result = await db.execute(
        select(func.count())
        .select_from(Conversation)
        .where(Conversation.channel == "voice", Conversation.started_at >= today_start)
    )
    calls_today = calls_today_result.scalar_one()

    leads_today_result = await db.execute(
        select(func.count()).select_from(Lead).where(Lead.created_at >= today_start)
    )
    leads_today = leads_today_result.scalar_one()

    # usd_spend_today — approximate cost over today's persisted messages.
    cost_result = await db.execute(
        select(
            func.coalesce(func.sum(Message.tokens_in), 0),
            func.coalesce(func.sum(Message.tokens_out), 0),
            func.coalesce(func.sum(Message.audio_secs), 0.0),
        ).where(Message.created_at >= today_start)
    )
    tokens_in_sum, tokens_out_sum, audio_secs_sum = cost_result.one()
    usd_spend_today = (
        float(tokens_in_sum) * _INPUT_USD_PER_TOKEN
        + float(tokens_out_sum) * _OUTPUT_USD_PER_TOKEN
        + float(audio_secs_sum) * _REALTIME_AUDIO_USD_PER_SECOND
    )

    # error_count_today — Redis counter keyed by today's UTC date.
    today_key = ERROR_COUNTER_KEY_FMT.format(date=date.today().isoformat())
    raw_err = await redis.get(today_key)
    try:
        error_count_today = int(raw_err) if raw_err is not None else 0
    except (TypeError, ValueError):
        error_count_today = 0

    return {
        "users_today": users_today,
        "calls_today": calls_today,
        "leads_today": leads_today,
        "p50_turn_latency_ms": None,
        "usd_spend_today": round(usd_spend_today, 6),
        "error_count_today": error_count_today,
    }


@router.get("/conversations")
async def list_conversations(
    db: DbDep,
    x_admin_token: str | None = Header(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    _verify_admin(x_admin_token)

    msg_count_subq = (
        select(Message.conversation_id, func.count().label("message_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )

    stmt = (
        select(Conversation, func.coalesce(msg_count_subq.c.message_count, 0))
        .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
        .order_by(Conversation.started_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await db.execute(stmt)).all()

    return [
        {
            **ConversationOut.model_validate(conv).model_dump(mode="json"),
            "message_count": int(count),
        }
        for conv, count in rows
    ]


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> list[MessageOut]:
    _verify_admin(x_admin_token)

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = (await db.execute(stmt)).scalars().all()
    return [MessageOut.model_validate(m) for m in messages]


@router.get("/leads")
async def list_leads(
    db: DbDep,
    x_admin_token: str | None = Header(None),
    intent: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LeadOut]:
    _verify_admin(x_admin_token)

    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    if intent:
        stmt = stmt.where(Lead.intent == intent)

    leads = (await db.execute(stmt)).scalars().all()
    return [LeadOut.model_validate(lead) for lead in leads]


@router.get("/leads.csv")
async def export_leads_csv(
    db: DbDep,
    x_admin_token: str | None = Header(None),
    intent: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> StreamingResponse:
    """Same data as GET /admin/leads but rendered as CSV for download."""
    _verify_admin(x_admin_token)

    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    if intent:
        stmt = stmt.where(Lead.intent == intent)
    leads = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "phone", "intent", "created_at"])
    for lead in leads:
        writer.writerow(
            [
                str(lead.id),
                lead.name or "",
                lead.phone or "",
                lead.intent or "",
                lead.created_at.isoformat() if lead.created_at else "",
            ]
        )
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


@router.get("/settings")
async def get_settings(
    x_admin_token: str | None = Header(None),
) -> dict:
    _verify_admin(x_admin_token)
    return {
        "environment": settings.environment,
        "default_org_id": str(settings.default_org_id),
        "log_level": settings.log_level,
    }


# ---------------------------------------------------------------------------
# §2.6 — Day 1 control-plane endpoints
# ---------------------------------------------------------------------------


@router.get("/prompts", response_model=PromptsOut)
async def get_prompts(
    x_admin_token: str | None = Header(None),
) -> PromptsOut:
    """Read-only view of the active system prompts."""
    _verify_admin(x_admin_token)
    return PromptsOut(
        base=OUTBOUND_CALL_PROMPT,
        voice_append=VOICE_APPEND,
        whatsapp_append=WHATSAPP_APPEND,
    )


@router.get("/tools")
async def get_tools(
    x_admin_token: str | None = Header(None),
) -> list[dict]:
    """Read-only view of the registered tool schemas."""
    _verify_admin(x_admin_token)
    return TOOL_DEFINITIONS


@router.get("/escalations")
async def get_escalations(
    db: DbDep,
    redis: RedisDep,
    x_admin_token: str | None = Header(None),
) -> dict:
    """Return recent escalation Lead rows plus the live human_handoff_queue."""
    _verify_admin(x_admin_token)

    stmt = (
        select(Lead)
        .where(Lead.intent == "escalation")
        .order_by(Lead.created_at.desc())
        .limit(50)
    )
    lead_rows = (await db.execute(stmt)).scalars().all()
    recent_leads = [LeadOut.model_validate(lead).model_dump(mode="json") for lead in lead_rows]

    # LRANGE for inspection — non-destructive; "Mark Handled" UI uses a separate
    # endpoint (LREM) added Day 4.
    raw_queue = await redis.lrange(HUMAN_HANDOFF_QUEUE, 0, -1)
    queue: list[object] = []
    for entry in raw_queue:
        # Entries are pushed as JSON by transfer_to_human; fall back to raw string
        # if a worker happens to push a plain string.
        try:
            queue.append(json.loads(entry))
        except (TypeError, ValueError):
            queue.append(entry)

    return {"recent_leads": recent_leads, "queue": queue}


# ---------------------------------------------------------------------------
# §3.6 — Day 2 control-plane endpoints
# ---------------------------------------------------------------------------


@router.post("/outbound/whatsapp", response_model=OutboundWhatsappOut)
@limiter.limit("30/minute")
async def outbound_whatsapp(
    request: Request,
    payload: OutboundWhatsappIn,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> OutboundWhatsappOut:
    """Send an outbound WhatsApp message and persist the assistant turn.

    Find-or-create the user + open conversation, persist a ``Message`` row,
    then (when ``meta_access_token`` is configured) hand the body to
    ``wa_client.send_text``. When the token is unset we keep the stub
    behaviour — useful for local development without Meta credentials.
    """
    _verify_admin(x_admin_token)

    org_id = UUID(settings.default_org_id)

    # Find or create the recipient user under the default org.
    user_stmt = select(User).where(User.org_id == org_id, User.phone == payload.phone)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if user is None:
        user = User(org_id=org_id, phone=payload.phone)
        db.add(user)
        await db.flush()

    # Find an open WhatsApp conversation for this user, otherwise open a new one.
    conv_stmt = (
        select(Conversation)
        .where(
            Conversation.org_id == org_id,
            Conversation.user_id == user.id,
            Conversation.channel == "whatsapp",
            Conversation.ended_at.is_(None),
        )
        .order_by(Conversation.started_at.desc())
        .limit(1)
    )
    conversation = (await db.execute(conv_stmt)).scalar_one_or_none()
    if conversation is None:
        conversation = Conversation(org_id=org_id, user_id=user.id, channel="whatsapp")
        db.add(conversation)
        await db.flush()

    # What we record in history: the literal text, or a marker for templates.
    body_for_record = payload.text or f"[template:{payload.template_name}]"
    message = Message(
        org_id=org_id,
        conversation_id=conversation.id,
        user_id=user.id,
        role="assistant",
        content=body_for_record,
        channel="whatsapp",
    )
    db.add(message)
    await db.commit()

    # Local-dev fallback: if Meta creds are missing, keep the stub response.
    if not settings.meta_access_token:
        logger.warning(
            "outbound_whatsapp_meta_token_unset",
            phone=payload.phone,
            reason="skipping_real_send",
        )
        return OutboundWhatsappOut(status="queued", phone=payload.phone, text=body_for_record)

    # Real send. A template is the ONLY way to reach a user OUTSIDE the 24-hour
    # customer-service window (free-form text raises Meta error 131047); inside
    # the window, plain text is fine. The caller chooses by supplying
    # template_name (template) or text (free-form) — enforced by the schema.
    try:
        if payload.template_name:
            graph_response = await wa_client.send_template(
                payload.phone,
                template_name=payload.template_name,
                language_code=payload.template_lang,
                body_params=payload.template_params,
            )
        else:
            # text is guaranteed non-None here by OutboundWhatsappIn's validator.
            graph_response = await wa_client.send_text(payload.phone, payload.text or "")
    except httpx.HTTPError as exc:
        logger.exception(
            "outbound_whatsapp_send_failed",
            phone=payload.phone,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail="WhatsApp send failed") from exc

    wa_message_id: str | None = None
    messages_block = graph_response.get("messages")
    if isinstance(messages_block, list) and messages_block:
        first = messages_block[0]
        if isinstance(first, dict):
            raw_id = first.get("id")
            if isinstance(raw_id, str):
                wa_message_id = raw_id

    logger.info(
        "outbound_whatsapp_sent",
        phone=payload.phone,
        wa_message_id=wa_message_id,
    )
    return OutboundWhatsappOut(
        status="sent",
        phone=payload.phone,
        text=body_for_record,
        wa_message_id=wa_message_id,
    )


# ---------------------------------------------------------------------------
# §4.7 — Day 3 control-plane endpoints
# ---------------------------------------------------------------------------


@router.post("/outbound/call", response_model=OutboundCallOut)
@limiter.limit("10/minute")
async def outbound_call(
    request: Request,
    payload: OutboundCallIn,
    x_admin_token: str | None = Header(None),
) -> OutboundCallOut:
    """Place an outbound voice call via Plivo.

    Local-dev fallback: when Plivo credentials + a from-number are not all
    configured, return a STUB response (same convention as
    ``outbound_whatsapp`` skipping the real send when ``meta_access_token`` is
    unset). The moment ``PLIVO_AUTH_ID`` / ``PLIVO_AUTH_TOKEN`` /
    ``PLIVO_PHONE_NUMBER`` are set, real calls go out automatically.

    When Plivo answers it fetches ``{PUBLIC_BASE_URL}/voice/answer`` — see
    ``channels/voice/webhook.py`` — which currently speaks a test message.
    """
    _verify_admin(x_admin_token)

    if not voice_plivo.is_configured():
        logger.warning(
            "outbound_call_plivo_not_configured",
            to=payload.to_phone,
            reason="skipping_real_call",
        )
        return OutboundCallOut(call_sid=f"STUB-{uuid4()}", status="stub")

    answer_url = f"{settings.public_base_url.rstrip('/')}/voice/answer"
    try:
        result = await voice_plivo.initiate_call(payload.to_phone, answer_url)
    except httpx.HTTPError as exc:
        logger.exception(
            "outbound_call_failed",
            to=payload.to_phone,
            error=str(exc),
        )
        raise HTTPException(status_code=502, detail="Outbound call failed") from exc

    request_uuid = result.get("request_uuid")
    call_sid = request_uuid if isinstance(request_uuid, str) else str(uuid4())
    return OutboundCallOut(call_sid=call_sid, status="queued")


# ---------------------------------------------------------------------------
# §5.6 — Day 4 control-plane endpoints (kill switch)
# ---------------------------------------------------------------------------


@router.post("/kill-switch", response_model=KillSwitchOut)
async def set_kill_switch(
    payload: KillSwitchIn,
    redis: RedisDep,
    x_admin_token: str | None = Header(None),
) -> KillSwitchOut:
    """Engage or release the global kill switch."""
    _verify_admin(x_admin_token)
    if payload.enabled:
        await redis.set(KILL_SWITCH_KEY, "1")
    else:
        await redis.delete(KILL_SWITCH_KEY)
    return KillSwitchOut(enabled=payload.enabled)


@router.get("/kill-switch", response_model=KillSwitchOut)
async def get_kill_switch(
    redis: RedisDep,
    x_admin_token: str | None = Header(None),
) -> KillSwitchOut:
    """Return current kill-switch state for the frontend banner."""
    _verify_admin(x_admin_token)
    value = await redis.get(KILL_SWITCH_KEY)
    return KillSwitchOut(enabled=value is not None)
