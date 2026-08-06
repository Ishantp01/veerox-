from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import openpyxl
import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import String, case, cast, func, or_, select

from apps.api.channels.voice import plivo_client as voice_plivo
from apps.api.channels.whatsapp import client as wa_client
from apps.api.config import settings
from apps.api.core.llm import chat_completion
from apps.api.core.prompts import OUTBOUND_CALL_PROMPT, VOICE_APPEND, WHATSAPP_APPEND
from apps.api.core.tools import (
    TOOL_DEFINITIONS,
    _normalize_phone,
)
from apps.api.core.usage import get_monthly_usage
from apps.api.db.models import CallCampaign, CampaignTarget, Conversation, Lead, Message, User
from apps.api.deps import (
    AnalyticsScopeDep,
    DbDep,
    RedisDep,
    RequestOrgDep,
    enforce_plan_limit,
    verify_admin_or_session,
)
from apps.api.rate_limit import limiter
from apps.api.redis_client import ERROR_COUNTER_KEY_FMT
from apps.api.schemas.admin import (
    CallingSettingsOut,
    KillSwitchIn,
    KillSwitchOut,
    OutboundCallIn,
    OutboundCallOut,
    OutboundWhatsappIn,
    OutboundWhatsappOut,
    PromptsOut,
    WhatsAppSettingsOut,
)
from apps.api.schemas.campaign import (
    CampaignCounts,
    CampaignCreateResult,
    CampaignDetailOut,
    CampaignOut,
    CampaignStatusUpdateOut,
    CampaignTargetOut,
)
from apps.api.schemas.conversation import ConversationOut, ConversationSummaryOut, MessageOut
from apps.api.schemas.lead import (
    LEAD_QUALIFICATION_STATUSES,
    LEAD_STATUSES,
    LeadBulkImportIn,
    LeadDetailOut,
    LeadOut,
    LeadUpdateIn,
)
from apps.api.schemas.reports import ReportsCampaignRow, ReportsTimeseriesPoint

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(verify_admin_or_session)]
)

# Redis keys / channels used by the control plane.
KILL_SWITCH_KEY = "veerox:kill_switch"
HUMAN_HANDOFF_QUEUE = "human_handoff_queue"

# Cost constants used by usd_spend_today. These mirror the planned values from
# implementation-plan.md §5.6 and intentionally live here rather than in
# apps/api/core/costs.py because that file is owned by worker 1 and may not
# exist yet. Once core/costs.py lands these can be re-imported from there.
_INPUT_USD_PER_TOKEN = 2.50 / 1_000_000  # $2.50 / 1M input tokens
_OUTPUT_USD_PER_TOKEN = 10.00 / 1_000_000  # $10.00 / 1M output tokens
_REALTIME_AUDIO_USD_PER_SECOND = 0.30 / 60.0  # $0.30 / minute of realtime audio


def _today_start_utc() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/stats")
async def get_stats(
    db: DbDep,
    redis: RedisDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
) -> dict:
    """Today's activity counters. Scoped to the caller's own org for a
    normal customer, platform-wide only for the platform admin — see
    `resolve_analytics_scope_org_id`, where `scope_org_id is None` is what
    "count every org" means.
    """
    today_start = _today_start_utc()

    def scoped(stmt, model):  # noqa: ANN001, ANN202 — SQLAlchemy Select generics
        return stmt if scope_org_id is None else stmt.where(model.org_id == scope_org_id)

    # Below: each table gets exactly one round trip regardless of how many
    # counters are pulled from it (conditional SUM/CASE in place of separate
    # queries) — DB is a remote Neon instance (see .env), so every extra
    # round trip is real latency on a page loaded on every dashboard visit.
    def _count_if(condition):  # noqa: ANN001, ANN202 — SQLAlchemy boolean expr
        return func.sum(case((condition, 1), else_=0))

    users_today_result = await db.execute(
        scoped(
            select(func.count()).select_from(User).where(User.created_at >= today_start),
            User,
        )
    )
    users_today = users_today_result.scalar_one()

    calls_today_result = await db.execute(
        scoped(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.channel == "voice", Conversation.started_at >= today_start),
            Conversation,
        )
    )
    calls_today = calls_today_result.scalar_one()

    message_stats_result = await db.execute(
        scoped(
            select(
                func.coalesce(_count_if(Message.channel == "whatsapp"), 0),
                func.coalesce(func.sum(Message.tokens_in), 0),
                func.coalesce(func.sum(Message.tokens_out), 0),
                func.coalesce(func.sum(Message.audio_secs), 0.0),
            ).where(Message.created_at >= today_start),
            Message,
        )
    )
    whatsapp_messages_today, tokens_in_sum, tokens_out_sum, audio_secs_sum = (
        message_stats_result.one()
    )
    usd_spend_today = (
        float(tokens_in_sum) * _INPUT_USD_PER_TOKEN
        + float(tokens_out_sum) * _OUTPUT_USD_PER_TOKEN
        + float(audio_secs_sum) * _REALTIME_AUDIO_USD_PER_SECOND
    )

    lead_stats_result = await db.execute(
        scoped(
            select(
                func.count(),
                func.coalesce(_count_if(Lead.channel == "voice"), 0),
                func.coalesce(_count_if(Lead.channel == "whatsapp"), 0),
            ).where(Lead.created_at >= today_start),
            Lead,
        )
    )
    leads_today, leads_today_voice, leads_today_whatsapp = lead_stats_result.one()

    # error_count_today — Redis counter keyed by today's UTC date. Written by
    # apps.api.redis_client.record_error(), called from each channel/worker's
    # top-level catch-all (whatsapp adapter, voice realtime bridge, campaign
    # dialer tick). It's a single platform-wide counter with no org
    # dimension, so it's only meaningful — and only reported — for the
    # platform admin; a customer org would otherwise see error counts driven
    # by other tenants' traffic.
    error_count_today = 0
    if scope_org_id is None:
        today_key = ERROR_COUNTER_KEY_FMT.format(date=datetime.now(UTC).date().isoformat())
        raw_err = await redis.get(today_key)
        try:
            error_count_today = int(raw_err) if raw_err is not None else 0
        except (TypeError, ValueError):
            error_count_today = 0

    return {
        "users_today": users_today,
        "calls_today": calls_today,
        "whatsapp_messages_today": whatsapp_messages_today,
        "leads_today": leads_today,
        "leads_today_voice": leads_today_voice,
        "leads_today_whatsapp": leads_today_whatsapp,
        "p50_turn_latency_ms": None,
        "usd_spend_today": round(usd_spend_today, 6),
        "error_count_today": error_count_today,
    }


@router.get("/reports/timeseries", response_model=list[ReportsTimeseriesPoint])
async def get_reports_timeseries(
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
    days: int = Query(30, ge=1, le=365),
) -> list[ReportsTimeseriesPoint]:
    """Daily trend data for the sales reports page — the historical
    counterpart to GET /admin/stats, which only ever covers "today". Carries
    the same org scoping: a customer sees only their own org's trend, the
    platform admin sees every org's combined.

    Buckets by ``func.date(...)`` rather than ``date_trunc`` so the same
    query works against both Postgres (production) and SQLite (tests).
    """
    since = datetime.now(UTC) - timedelta(days=days)

    def scoped(stmt, model):  # noqa: ANN001, ANN202 — SQLAlchemy Select generics
        return stmt if scope_org_id is None else stmt.where(model.org_id == scope_org_id)

    # Keys are cast to str immediately — func.date(...) returns a raw
    # `datetime.date` on Postgres, but `all_days`/the lookups below are
    # strings, so leaving these as date-object keys means every .get(day)
    # below silently misses and falls back to 0 (calls/whatsapp_messages
    # always reading 0 on the reports chart even with real activity).
    calls_by_day = {
        str(day_val): count_val
        for day_val, count_val in (
            await db.execute(
                scoped(
                    select(func.date(Conversation.started_at), func.count()).where(
                        Conversation.channel == "voice", Conversation.started_at >= since
                    ),
                    Conversation,
                ).group_by(func.date(Conversation.started_at))
            )
        ).all()
    }
    # One round trip per table instead of one per counter (conditional SUM in
    # place of a separate query for each breakdown) — DB is a remote Neon
    # instance (see .env), so every extra query here is real page-load
    # latency on the Reports page.
    def _count_if(condition):  # noqa: ANN001, ANN202 — SQLAlchemy boolean expr
        return func.sum(case((condition, 1), else_=0))

    message_rows = (
        await db.execute(
            scoped(
                select(
                    func.date(Message.created_at),
                    func.coalesce(_count_if(Message.channel == "whatsapp"), 0),
                    func.coalesce(func.sum(Message.tokens_in), 0),
                    func.coalesce(func.sum(Message.tokens_out), 0),
                    func.coalesce(func.sum(Message.audio_secs), 0.0),
                ).where(Message.created_at >= since),
                Message,
            ).group_by(func.date(Message.created_at))
        )
    ).all()
    whatsapp_by_day: dict[str, int] = {}
    usd_spend_by_day: dict[str, float] = {}
    for day_val, whatsapp_count, tokens_in_sum, tokens_out_sum, audio_secs_sum in message_rows:
        day = str(day_val)
        whatsapp_by_day[day] = whatsapp_count
        usd_spend_by_day[day] = (
            float(tokens_in_sum) * _INPUT_USD_PER_TOKEN
            + float(tokens_out_sum) * _OUTPUT_USD_PER_TOKEN
            + float(audio_secs_sum) * _REALTIME_AUDIO_USD_PER_SECOND
        )

    lead_rows = (
        await db.execute(
            scoped(
                select(
                    func.date(Lead.created_at),
                    Lead.channel,
                    func.count(),
                    func.coalesce(_count_if(Lead.status == "qualified"), 0),
                ).where(Lead.created_at >= since),
                Lead,
            ).group_by(func.date(Lead.created_at), Lead.channel)
        )
    ).all()
    leads_voice_by_day: dict[str, int] = {}
    leads_whatsapp_by_day: dict[str, int] = {}
    qualified_by_day: dict[str, int] = {}
    for day_val, channel_val, count_val, qualified_val in lead_rows:
        day = str(day_val)
        if channel_val in ("voice", "whatsapp"):
            target = leads_voice_by_day if channel_val == "voice" else leads_whatsapp_by_day
            target[day] = count_val
        qualified_by_day[day] = qualified_by_day.get(day, 0) + qualified_val

    all_days = sorted(
        {str(d) for d in calls_by_day}
        | {str(d) for d in whatsapp_by_day}
        | set(leads_voice_by_day)
        | set(leads_whatsapp_by_day)
        | set(qualified_by_day)
        | set(usd_spend_by_day)
    )
    return [
        ReportsTimeseriesPoint(
            date=day,
            calls=calls_by_day.get(day, 0),
            whatsapp_messages=whatsapp_by_day.get(day, 0),
            leads_voice=leads_voice_by_day.get(day, 0),
            leads_whatsapp=leads_whatsapp_by_day.get(day, 0),
            qualified_count=qualified_by_day.get(day, 0),
            usd_spend=round(usd_spend_by_day.get(day, 0.0), 6),
        )
        for day in all_days
    ]


@router.get("/reports/campaigns", response_model=list[ReportsCampaignRow])
async def get_reports_campaigns(
    db: DbDep,
    org: RequestOrgDep,
    x_admin_token: str | None = Header(None),
) -> list[ReportsCampaignRow]:
    """Per-campaign conversion table for the reports page — reuses
    ``_campaign_counts`` (defined further down) so this stays consistent
    with the counts already shown on the campaigns list."""
    org_id = org
    stmt = (
        select(CallCampaign)
        .where(CallCampaign.org_id == org_id)
        .order_by(CallCampaign.created_at.desc())
    )
    campaigns = (await db.execute(stmt)).scalars().all()

    rows: list[ReportsCampaignRow] = []
    for campaign in campaigns:
        counts = await _campaign_counts(db, campaign.id)
        qualification_rate = (
            counts.qualified / counts.completed if counts.completed else None
        )
        rows.append(
            ReportsCampaignRow(
                id=campaign.id,
                name=campaign.name,
                channel=campaign.channel,
                status=campaign.status,
                counts=counts,
                qualification_rate=qualification_rate,
            )
        )
    return rows


@router.get("/conversations")
async def list_conversations(
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    msg_count_subq = (
        select(Message.conversation_id, func.count().label("message_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )

    stmt = (
        select(Conversation, func.coalesce(msg_count_subq.c.message_count, 0))
        .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
    )
    if scope_org_id is not None:
        stmt = stmt.where(Conversation.org_id == scope_org_id)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    stmt = stmt.order_by(Conversation.started_at.desc()).limit(limit).offset(offset)

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
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = (await db.execute(stmt)).scalars().all()
    return [MessageOut.model_validate(m) for m in messages]


_SUMMARY_SYSTEM_PROMPT = (
    "Summarize this customer conversation in 2-3 sentences for a sales rep "
    "reviewing it later. Cover what the customer wanted, anything promised or "
    "decided, and any next step. Plain prose, no headers or bullet points."
)


@router.post("/conversations/{conversation_id}/summarize", response_model=ConversationOut)
async def summarize_conversation(
    conversation_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> Conversation:
    """Generate (or regenerate) an AI summary of a conversation's transcript,
    on demand — not automatic, since not every conversation needs one."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = (await db.execute(stmt)).scalars().all()
    transcript = "\n".join(f"{m.role}: {m.content}" for m in messages if m.content)
    if not transcript:
        raise HTTPException(status_code=400, detail="Conversation has no messages to summarize")

    result = await chat_completion(
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    conversation.summary = (result.content or "").strip() or None
    await db.commit()
    await db.refresh(conversation)
    return conversation


_LEAD_STATUS_PATTERN = f"^({'|'.join(LEAD_STATUSES)})$"
_LEAD_QUALIFICATION_STATUS_PATTERN = f"^({'|'.join(LEAD_QUALIFICATION_STATUSES)})$"


def _lead_tag_clause(tag: str):
    """Match a tag inside `Lead.tags` (a JSON string array) by casting to
    text and checking for the quoted value — portable across SQLite (tests)
    and Postgres (prod) without needing JSONB containment operators.
    """
    return cast(Lead.tags, String).ilike(f'%"{tag}"%')


def _lead_search_clause(search: str):
    """Unified search box (UI) — matches leads whose intent OR tags contain
    the term, so one field can stand in for the separate intent/tag filters.
    """
    return or_(Lead.intent.ilike(f"%{search}%"), cast(Lead.tags, String).ilike(f"%{search}%"))


@router.get("/leads")
async def list_leads(
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
    intent: str | None = Query(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
    status: str | None = Query(None, pattern=_LEAD_STATUS_PATTERN),
    qualification_status: str | None = Query(None, pattern=_LEAD_QUALIFICATION_STATUS_PATTERN),
    tag: str | None = Query(None, description="Filter by a single tag"),
    search: str | None = Query(None, description="Match against intent or tags"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[LeadOut]:
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    if scope_org_id is not None:
        stmt = stmt.where(Lead.org_id == scope_org_id)
    if intent:
        stmt = stmt.where(Lead.intent.ilike(f"%{intent}%"))
    if channel:
        stmt = stmt.where(Lead.channel == channel)
    if status:
        stmt = stmt.where(Lead.status == status)
    if qualification_status:
        stmt = stmt.where(Lead.qualification_status == qualification_status)
    if tag:
        stmt = stmt.where(_lead_tag_clause(tag))
    if search:
        stmt = stmt.where(_lead_search_clause(search))

    leads = (await db.execute(stmt)).scalars().all()
    return [LeadOut.model_validate(lead) for lead in leads]


_SAMPLE_IMPORT_ROWS = [
    {"name": "Asha Verma", "phone": "+919876543210", "channel": "voice"},
    {"name": "Rohit Singh", "phone": "+919812345678", "channel": "whatsapp"},
]


@router.get("/leads/sample.csv")
async def sample_leads_csv(x_admin_token: str | None = Header(None)) -> StreamingResponse:
    """Blank-data template for POST /leads/import — same columns that
    endpoint reads (name, phone, channel), so a unified-page upload can mix
    call and WhatsApp rows in one file. Registered ahead of GET
    /leads/{lead_id} so its literal path isn't swallowed by that route's
    UUID path param.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "phone", "channel"])
    for row in _SAMPLE_IMPORT_ROWS:
        writer.writerow([row["name"], row["phone"], row["channel"]])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads-sample.csv"'},
    )


@router.get("/leads/sample.xlsx")
async def sample_leads_xlsx(x_admin_token: str | None = Header(None)) -> StreamingResponse:
    """Same template as GET /leads/sample.csv, as an .xlsx workbook."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Leads"
    sheet.append(["name", "phone", "channel"])
    for row in _SAMPLE_IMPORT_ROWS:
        sheet.append([row["name"], row["phone"], row["channel"]])
    for cell in sheet["B"][1:]:
        cell.number_format = "@"

    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="leads-sample.xlsx"'},
    )


@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
async def get_lead(
    lead_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> LeadDetailOut:
    """Lead detail — the captured fields plus that lead's conversation
    history (dashboard/CRM view). Conversations are joined via the shared
    ``user_id`` since Lead has no direct FK to Conversation.
    """
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    msg_count_subq = (
        select(Message.conversation_id, func.count().label("message_count"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    conv_stmt = (
        select(Conversation, func.coalesce(msg_count_subq.c.message_count, 0))
        .outerjoin(msg_count_subq, Conversation.id == msg_count_subq.c.conversation_id)
        .where(Conversation.user_id == lead.user_id)
        .order_by(Conversation.started_at.desc())
    )
    conv_rows = (await db.execute(conv_stmt)).all()
    conversations = [
        ConversationSummaryOut(
            **ConversationOut.model_validate(conv).model_dump(),
            message_count=int(count),
        )
        for conv, count in conv_rows
    ]

    return LeadDetailOut(**LeadOut.model_validate(lead).model_dump(), conversations=conversations)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: UUID,
    payload: LeadUpdateIn,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> LeadOut:
    """Update a lead's status and/or follow-up. Partial: only fields the
    caller actually sent are touched, so posting `{"status": "contacted"}`
    leaves follow_up_at/note untouched, while `{"follow_up_at": null}`
    explicitly clears it.
    """
    lead = (await db.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(lead, field, value)

    # Stamp qualified_at the moment a lead first enters "qualified", so the
    # caller doesn't have to set it explicitly on every qualification update.
    if updates.get("qualification_status") == "qualified" and lead.qualified_at is None:
        lead.qualified_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.get("/leads.csv")
async def export_leads_csv(
    db: DbDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
    intent: str | None = Query(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
    status: str | None = Query(None, pattern=_LEAD_STATUS_PATTERN),
    tag: str | None = Query(None, description="Filter by a single tag"),
    search: str | None = Query(None, description="Match against intent or tags"),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> StreamingResponse:
    """Same data as GET /admin/leads but rendered as CSV for download —
    including the same org scoping, so an export can't pull rows the list
    view wouldn't show."""
    stmt = select(Lead).order_by(Lead.created_at.desc()).limit(limit).offset(offset)
    if scope_org_id is not None:
        stmt = stmt.where(Lead.org_id == scope_org_id)
    if intent:
        stmt = stmt.where(Lead.intent.ilike(f"%{intent}%"))
    if channel:
        stmt = stmt.where(Lead.channel == channel)
    if status:
        stmt = stmt.where(Lead.status == status)
    if tag:
        stmt = stmt.where(_lead_tag_clause(tag))
    if search:
        stmt = stmt.where(_lead_search_clause(search))
    leads = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "phone", "intent", "tags", "channel", "status", "created_at"])
    for lead in leads:
        writer.writerow(
            [
                str(lead.id),
                lead.name or "",
                lead.phone or "",
                lead.intent or "",
                ",".join(lead.tags) if lead.tags else "",
                lead.channel or "",
                lead.status or "",
                lead.created_at.isoformat() if lead.created_at else "",
            ]
        )
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads.csv"'},
    )


def _normalize_import_row(raw_row: dict[str, object]) -> dict[str, str]:
    return {
        (k or "").strip().lower(): (str(v).strip() if v is not None else "")
        for k, v in raw_row.items()
    }


def _iter_csv_rows(raw: bytes) -> Iterator[tuple[int, dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not reader.fieldnames or "phone" not in {
        (f or "").strip().lower() for f in reader.fieldnames
    }:
        raise HTTPException(status_code=400, detail="File must include a 'phone' column")
    for row_num, raw_row in enumerate(reader, start=2):  # header occupies row 1
        yield row_num, _normalize_import_row(raw_row)


def _iter_xlsx_rows(raw: bytes) -> Iterator[tuple[int, dict[str, str]]]:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read .xlsx file") from exc

    sheet_rows = workbook.active.iter_rows(values_only=True)
    header = next(sheet_rows, None) or ()
    headers = [(str(h).strip().lower() if h is not None else "") for h in header]
    if "phone" not in headers:
        raise HTTPException(status_code=400, detail="File must include a 'phone' column")

    for row_num, values in enumerate(sheet_rows, start=2):  # header occupies row 1
        yield row_num, _normalize_import_row(dict(zip(headers, values, strict=False)))


def _default_campaign_name() -> str:
    return f"Bulk import {datetime.now(UTC):%Y-%m-%d %H:%M}"


# Default qualification bar for campaigns auto-created from a plain lead
# import, when the caller doesn't supply their own ``criteria``.
_DEFAULT_IMPORT_CRITERIA = (
    "Prospect confirms genuine interest in our product/service and provides "
    "valid contact details for follow-up."
)


@router.post("/leads/import", response_model=CampaignCreateResult)
async def import_leads_file(
    db: DbDep,
    org: RequestOrgDep,
    file: UploadFile = File(...),
    x_admin_token: str | None = Header(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
    campaign_name: str | None = Form(None),
    criteria: str | None = Form(None),
) -> CampaignCreateResult:
    """Bulk-import leads from an uploaded CSV or Excel (.xlsx) file.

    Imported contacts are staged as an auto-generated campaign
    (``CallCampaign`` + ``CampaignTarget`` rows) rather than written straight
    to the CRM — the background dialer/dispatcher reaches out to each one and
    the AI's ``qualify_lead`` tool call is what promotes a target into a
    ``Lead`` row, and only when interested. This mirrors ``POST
    /admin/campaigns`` exactly; the only difference is this endpoint fills in
    a default campaign name/criteria when the caller doesn't supply one. For
    programmatic (non-file) bulk import, see ``POST /admin/leads/bulk``.

    ``channel`` is optional: per-channel pages (e.g. /calling/leads,
    /whatsapp/leads) pass it explicitly, forcing every row into that one
    channel. The unified CRM leads page omits it, since a single upload may
    mix call and WhatsApp rows — in that case each row's own ``channel``
    column picks its destination and the file is split into one campaign per
    channel found.
    """
    filename = (file.filename or "").lower()
    raw = await file.read()

    if filename.endswith(".csv"):
        rows = list(_iter_csv_rows(raw))
    elif filename.endswith(".xlsx"):
        rows = list(_iter_xlsx_rows(raw))
    else:
        raise HTTPException(status_code=400, detail="Only .csv or .xlsx files are supported")

    org_id = org
    name = campaign_name or _default_campaign_name()
    crit = criteria or _DEFAULT_IMPORT_CRITERIA

    if channel:
        return await _create_campaign_from_rows(
            db, org_id=org_id, name=name, criteria=crit, channel=channel, rows=rows
        )

    # No forced channel: split rows by their own "channel" column so one
    # upload can carry both call and WhatsApp leads at once.
    # If a row has no channel column, default to "voice" for backwards compatibility.
    grouped: dict[str, list[tuple[int, dict[str, str]]]] = {"voice": [], "whatsapp": []}
    errors: list[dict[str, str | int]] = []
    for row_num, row in rows:
        row_channel = (row.get("channel") or "voice").strip().lower()
        if row_channel not in grouped:
            errors.append(
                {
                    "row": row_num,
                    "reason": f"channel must be 'voice' or 'whatsapp', got '{row_channel or ''}'",
                }
            )
            continue
        grouped[row_channel].append((row_num, row))

    result: CampaignCreateResult | None = None
    imported = 0
    skipped = len(errors)
    for group_channel, group_rows in grouped.items():
        if not group_rows:
            continue
        group_result = await _create_campaign_from_rows(
            db,
            org_id=org_id,
            name=f"{name} ({group_channel})",
            criteria=crit,
            channel=group_channel,
            rows=group_rows,
        )
        imported += group_result.imported
        skipped += group_result.skipped
        errors.extend(group_result.errors)
        if result is None:
            result = group_result

    if result is None:
        raise HTTPException(
            status_code=400,
            detail="File must include a 'channel' column (voice or whatsapp) for each row",
        )

    return CampaignCreateResult(
        campaign=result.campaign, imported=imported, skipped=skipped, errors=errors
    )


@router.post("/leads/bulk", response_model=CampaignCreateResult)
async def import_leads_bulk(
    payload: LeadBulkImportIn,
    db: DbDep,
    org: RequestOrgDep,
    x_admin_token: str | None = Header(None),
) -> CampaignCreateResult:
    """Bulk-import leads from a JSON array — the programmatic counterpart to
    the CSV/Excel upload above, for callers integrating directly against the
    API rather than uploading a file. Same auto-campaign behavior as
    ``POST /admin/leads/import``.
    """
    org_id = org
    rows = (
        (row_num, {"phone": row.phone, "name": row.name or ""})
        for row_num, row in enumerate(payload.leads, start=1)
    )
    return await _create_campaign_from_rows(
        db,
        org_id=org_id,
        name=payload.campaign_name or _default_campaign_name(),
        criteria=payload.criteria or _DEFAULT_IMPORT_CRITERIA,
        channel=payload.channel or "voice",
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Campaigns — bulk-upload a lead list, auto-reach each one by voice or
# WhatsApp, and let the AI qualify them (core/tools.qualify_lead) against
# per-campaign criteria. Only qualified targets ever produce a Lead row.
# Every bulk-import entry point (this section's /campaigns, plus
# /leads/import and /leads/bulk above) funnels through
# _create_campaign_from_rows so none of them can bypass qualification.
# ---------------------------------------------------------------------------

# Plivo dials whatever's in `to` verbatim — a bare 10-digit number without a
# country code won't ring (mirrors the Dial page's zod regex, apps/web's
# calling/dial/page.tsx), so campaign uploads are validated the same way.
_E164_PATTERN = re.compile(r"^\+\d{8,15}$")


async def _campaign_counts(db: DbDep, campaign_id: UUID) -> CampaignCounts:
    status_stmt = (
        select(CampaignTarget.status, func.count())
        .where(CampaignTarget.campaign_id == campaign_id)
        .group_by(CampaignTarget.status)
    )
    tally = dict((await db.execute(status_stmt)).all())
    qualified_stmt = select(func.count()).where(
        CampaignTarget.campaign_id == campaign_id, CampaignTarget.qualified.is_(True)
    )
    qualified = (await db.execute(qualified_stmt)).scalar_one()
    return CampaignCounts(
        pending=tally.get("pending", 0),
        calling=tally.get("calling", 0),
        completed=tally.get("completed", 0),
        failed=tally.get("failed", 0),
        qualified=qualified,
    )


def _campaign_out(campaign: CallCampaign, counts: CampaignCounts) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        org_id=campaign.org_id,
        name=campaign.name,
        criteria=campaign.criteria,
        channel=campaign.channel,
        status=campaign.status,
        created_at=campaign.created_at,
        counts=counts,
    )


async def _create_campaign_from_rows(
    db: DbDep,
    *,
    org_id: UUID,
    name: str,
    criteria: str,
    channel: str,
    rows: Iterable[tuple[int, dict[str, str]]],
) -> CampaignCreateResult:
    """Shared core for every campaign-creation entry point (CSV/xlsx upload,
    JSON bulk import). Contacts are staged as ``CampaignTarget`` rows, not
    ``Lead`` rows — the background dialer/dispatcher calls or messages each
    one and the AI's ``qualify_lead`` tool call is what promotes a target
    into the CRM leads table, and only when interested.
    """
    campaign = CallCampaign(org_id=org_id, name=name, criteria=criteria, channel=channel)
    db.add(campaign)
    await db.flush()

    imported = 0
    errors: list[dict[str, str | int]] = []
    for row_num, row in rows:
        phone = row.get("phone", "")
        if not phone:
            errors.append({"row": row_num, "reason": "missing phone"})
            continue
        normalized = _normalize_phone(phone)
        if not _E164_PATTERN.match(normalized):
            errors.append(
                {
                    "row": row_num,
                    "reason": (
                        f"phone '{phone}' must include a country code in E.164 format, "
                        "e.g. +919876543210"
                    ),
                }
            )
            continue
        db.add(
            CampaignTarget(
                campaign_id=campaign.id,
                org_id=org_id,
                name=row.get("name") or None,
                phone=normalized,
            )
        )
        imported += 1

    await db.commit()

    logger.info(
        "admin_campaign_created",
        campaign_id=str(campaign.id),
        channel=channel,
        imported=imported,
        skipped=len(errors),
    )

    counts = await _campaign_counts(db, campaign.id)
    return CampaignCreateResult(
        campaign=_campaign_out(campaign, counts),
        imported=imported,
        skipped=len(errors),
        errors=errors,
    )


@router.post("/campaigns", response_model=CampaignCreateResult)
async def create_campaign(
    db: DbDep,
    org: RequestOrgDep,
    name: str = Form(...),
    criteria: str = Form(...),
    file: UploadFile = File(...),
    channel: str = Form("voice", pattern="^(voice|whatsapp)$"),
    x_admin_token: str | None = Header(None),
) -> CampaignCreateResult:
    """Create a campaign from an uploaded CSV/Excel contact list.

    ``channel`` selects which background worker drives the resulting
    targets: the voice dialer (apps/api/workers/campaign_dialer.py) or the
    WhatsApp dispatcher (apps/api/workers/whatsapp_dispatcher.py). Defaults
    to "voice" to match this endpoint's original (pre-WhatsApp) behavior.
    """
    filename = (file.filename or "").lower()
    raw = await file.read()
    if filename.endswith(".csv"):
        rows = _iter_csv_rows(raw)
    elif filename.endswith(".xlsx"):
        rows = _iter_xlsx_rows(raw)
    else:
        raise HTTPException(status_code=400, detail="Only .csv or .xlsx files are supported")

    org_id = org
    campaign_count_result = await db.execute(
        select(func.count()).select_from(CallCampaign).where(CallCampaign.org_id == org_id)
    )
    await enforce_plan_limit(db, org_id, "max_campaigns", campaign_count_result.scalar_one())
    return await _create_campaign_from_rows(
        db, org_id=org_id, name=name, criteria=criteria, channel=channel, rows=rows
    )


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(
    db: DbDep,
    org: RequestOrgDep,
    x_admin_token: str | None = Header(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
) -> list[CampaignOut]:
    org_id = org
    stmt = (
        select(CallCampaign)
        .where(CallCampaign.org_id == org_id)
        .order_by(CallCampaign.created_at.desc())
    )
    if channel:
        stmt = stmt.where(CallCampaign.channel == channel)
    campaigns = (await db.execute(stmt)).scalars().all()
    return [_campaign_out(c, await _campaign_counts(db, c.id)) for c in campaigns]


_SAMPLE_CAMPAIGN_ROWS = [
    {"name": "Asha Verma", "phone": "+919876543210"},
    {"name": "Rohit Singh", "phone": "+919812345678"},
]


@router.get("/campaigns/sample.csv")
async def sample_campaign_csv(x_admin_token: str | None = Header(None)) -> StreamingResponse:
    """Blank-data template for POST /campaigns' contact-list upload — just
    name/phone, since the campaign's channel (voice or WhatsApp) is picked
    once via the form's Channel select, not per row. Registered ahead of GET
    /campaigns/{campaign_id} so its literal path isn't swallowed by that
    route's UUID path param.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "phone"])
    for row in _SAMPLE_CAMPAIGN_ROWS:
        writer.writerow([row["name"], row["phone"]])
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="campaign-contacts-sample.csv"'},
    )


@router.get("/campaigns/sample.xlsx")
async def sample_campaign_xlsx(x_admin_token: str | None = Header(None)) -> StreamingResponse:
    """Same template as GET /campaigns/sample.csv, as an .xlsx workbook."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Contacts"
    sheet.append(["name", "phone"])
    for row in _SAMPLE_CAMPAIGN_ROWS:
        sheet.append([row["name"], row["phone"]])
    for cell in sheet["B"][1:]:
        cell.number_format = "@"

    buf = io.BytesIO()
    workbook.save(buf)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="campaign-contacts-sample.xlsx"'},
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailOut)
async def get_campaign(
    campaign_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> CampaignDetailOut:
    campaign = await db.get(CallCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    stmt = (
        select(CampaignTarget)
        .where(CampaignTarget.campaign_id == campaign_id)
        .order_by(CampaignTarget.created_at)
    )
    targets = (await db.execute(stmt)).scalars().all()
    counts = await _campaign_counts(db, campaign_id)

    base = _campaign_out(campaign, counts)
    return CampaignDetailOut(
        **base.model_dump(),
        targets=[CampaignTargetOut.model_validate(t) for t in targets],
    )


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignStatusUpdateOut)
async def pause_campaign(
    campaign_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> CampaignStatusUpdateOut:
    campaign = await db.get(CallCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "paused"
    await db.commit()
    return CampaignStatusUpdateOut(id=campaign.id, status=campaign.status)


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignStatusUpdateOut)
async def resume_campaign(
    campaign_id: UUID,
    db: DbDep,
    x_admin_token: str | None = Header(None),
) -> CampaignStatusUpdateOut:
    campaign = await db.get(CallCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "running"
    await db.commit()
    return CampaignStatusUpdateOut(id=campaign.id, status=campaign.status)


@router.get("/settings")
async def get_settings(
    x_admin_token: str | None = Header(None),
) -> dict:
    return {
        "environment": settings.environment,
        "default_org_id": str(settings.default_org_id),
        "log_level": settings.log_level,
    }


@router.get("/settings/whatsapp", response_model=WhatsAppSettingsOut)
async def get_whatsapp_settings(
    x_admin_token: str | None = Header(None),
) -> WhatsAppSettingsOut:
    """Read-only status of the WhatsApp/Meta channel config for the
    /whatsapp/settings page. View-only: these are Render env vars
    (apps/api/config.py), not DB rows — a persisted override would create a
    second source of truth that can silently diverge from them (see the
    OPENAI_CHAT_MODEL incident in project notes).
    """
    return WhatsAppSettingsOut(
        configured=bool(settings.meta_access_token and settings.meta_phone_number_id),
        app_id_configured=bool(settings.meta_app_id),
        app_secret_configured=bool(settings.meta_app_secret),
        verify_token_configured=bool(settings.meta_verify_token),
        access_token_configured=bool(settings.meta_access_token),
        phone_number_id=settings.meta_phone_number_id,
        whatsapp_business_account_id=settings.meta_whatsapp_business_account_id,
        graph_api_version=settings.meta_graph_api_version,
        webhook_url=f"{settings.public_base_url.rstrip('/')}/webhook/whatsapp",
    )


@router.get("/settings/calling", response_model=CallingSettingsOut)
async def get_calling_settings(
    x_admin_token: str | None = Header(None),
) -> CallingSettingsOut:
    """Read-only status of the Plivo voice channel config for the
    /calling/settings page — see get_whatsapp_settings for why this is
    view-only rather than editable.
    """
    return CallingSettingsOut(
        configured=voice_plivo.is_configured(),
        auth_id_configured=bool(settings.plivo_auth_id),
        auth_token_configured=bool(settings.plivo_auth_token),
        phone_number=settings.plivo_phone_number,
        answer_webhook_url=f"{settings.public_base_url.rstrip('/')}/voice/answer",
    )


# ---------------------------------------------------------------------------
# §2.6 — Day 1 control-plane endpoints
# ---------------------------------------------------------------------------


@router.get("/prompts", response_model=PromptsOut)
async def get_prompts(
    x_admin_token: str | None = Header(None),
) -> PromptsOut:
    """Read-only view of the active system prompts."""
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
    return TOOL_DEFINITIONS


@router.get("/escalations")
async def get_escalations(
    db: DbDep,
    redis: RedisDep,
    scope_org_id: AnalyticsScopeDep,
    x_admin_token: str | None = Header(None),
    channel: str | None = Query(None, pattern="^(voice|whatsapp)$"),
) -> dict:
    """Return recent escalation Lead rows plus the live human_handoff_queue,
    both scoped to the caller's own org unless they're the platform admin.
    The queue is a single Redis list with no per-org key, so it's filtered
    in Python on the `org_id` each entry carries (written by
    core/tools.py's transfer_to_human).
    """
    stmt = (
        select(Lead)
        .where(Lead.intent == "escalation")
        .order_by(Lead.created_at.desc())
        .limit(50)
    )
    if scope_org_id is not None:
        stmt = stmt.where(Lead.org_id == scope_org_id)
    if channel:
        stmt = stmt.where(Lead.channel == channel)
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
            parsed = json.loads(entry)
        except (TypeError, ValueError):
            parsed = entry
        if channel and isinstance(parsed, dict) and parsed.get("channel") != channel:
            continue
        if scope_org_id is not None:
            # A non-dict entry (plain-string fallback above) has no org to
            # check, so it can only be shown platform-wide.
            if not isinstance(parsed, dict) or parsed.get("org_id") != str(scope_org_id):
                continue
        queue.append(parsed)

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
    org: RequestOrgDep,
    x_admin_token: str | None = Header(None),
) -> OutboundWhatsappOut:
    """Send an outbound WhatsApp message and persist the assistant turn.

    Find-or-create the user + open conversation, persist a ``Message`` row,
    then (when ``meta_access_token`` is configured) hand the body to
    ``wa_client.send_text``. When the token is unset we keep the stub
    behaviour — useful for local development without Meta credentials.
    """
    org_id = org
    usage = await get_monthly_usage(db, org_id)
    await enforce_plan_limit(db, org_id, "max_whatsapp_messages_per_month", usage.whatsapp_messages)

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
    db: DbDep,
    org: RequestOrgDep,
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
    org_id = org
    usage = await get_monthly_usage(db, org_id)
    await enforce_plan_limit(db, org_id, "max_call_minutes_per_month", usage.call_minutes)

    if not voice_plivo.is_configured():
        logger.warning(
            "outbound_call_plivo_not_configured",
            to=payload.to_phone,
            reason="skipping_real_call",
        )
        return OutboundCallOut(call_sid=f"STUB-{uuid4()}", status="stub")

    # org_id travels on the answer_url so the realtime bridge attributes this
    # call's usage to the org that actually placed it (see
    # channels/voice/webhook.py / realtime_bridge.py) rather than the
    # platform default — this is the one call site with no campaign context
    # to resolve org from instead.
    answer_url = f"{settings.public_base_url.rstrip('/')}/voice/answer?org_id={org_id}"
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
    value = await redis.get(KILL_SWITCH_KEY)
    return KillSwitchOut(enabled=value is not None)
