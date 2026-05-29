"""Generate the Veerox AI production execution plan as a polished PDF."""
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

OUT = r"C:\Users\shiva\OneDrive\Desktop\veerox-core\Veerox-AI-Production-Plan.pdf"

# ----- palette -----
INDIGO = colors.HexColor("#4f46e5")
INDIGO_DK = colors.HexColor("#3730a3")
SLATE = colors.HexColor("#334155")
SLATE_LT = colors.HexColor("#64748b")
SLATE_BG = colors.HexColor("#f1f5f9")
GREEN = colors.HexColor("#059669")
AMBER = colors.HexColor("#d97706")
RED = colors.HexColor("#dc2626")
WHITE = colors.white

styles = getSampleStyleSheet()


def S(name, **kw):
    base = kw.pop("parent", styles["Normal"])
    return ParagraphStyle(name, parent=base, **kw)


h1 = S("h1", fontName="Helvetica-Bold", fontSize=20, textColor=INDIGO_DK,
       spaceBefore=18, spaceAfter=8, leading=24)
h2 = S("h2", fontName="Helvetica-Bold", fontSize=14, textColor=INDIGO,
       spaceBefore=14, spaceAfter=6, leading=18)
body = S("body", fontSize=10, textColor=SLATE, leading=15, spaceAfter=6)
small = S("small", fontSize=8.5, textColor=SLATE_LT, leading=12)
cell = S("cell", fontSize=8.5, textColor=SLATE, leading=11)
cellb = S("cellb", fontName="Helvetica-Bold", fontSize=8.5, textColor=WHITE, leading=11)
cover_t = S("cover_t", fontName="Helvetica-Bold", fontSize=34, textColor=WHITE,
            alignment=TA_CENTER, leading=40)
cover_s = S("cover_s", fontSize=14, textColor=colors.HexColor("#c7d2fe"),
            alignment=TA_CENTER, leading=20)

story = []


def para(t, st=body):
    story.append(Paragraph(t, st))


def gap(h=6):
    story.append(Spacer(1, h))


def mktable(data, widths, header=True, header_color=INDIGO):
    rows = []
    if header:
        rows.append([Paragraph(c, cellb) for c in data[0]])
        rows += [[Paragraph(str(c), cell) for c in r] for r in data[1:]]
    else:
        rows = [[Paragraph(str(c), cell) for c in r] for r in data]
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    sty = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, SLATE_BG]),
    ]
    if header:
        sty += [("BACKGROUND", (0, 0), (-1, 0), header_color),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7)]
    t.setStyle(TableStyle(sty))
    story.append(t)


# ============ COVER ============
def cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INDIGO_DK)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
    canvas.setFillColor(INDIGO)
    canvas.rect(0, A4[1] - 260, A4[0], 260, fill=1, stroke=0)
    # logo box
    canvas.setFillColor(WHITE)
    canvas.roundRect(A4[0] / 2 - 32, A4[1] - 175, 64, 64, 12, fill=1, stroke=0)
    canvas.setFillColor(INDIGO_DK)
    canvas.setFont("Helvetica-Bold", 38)
    canvas.drawCentredString(A4[0] / 2, A4[1] - 162, "V")
    canvas.restoreState()


story.append(Spacer(1, 250))
para("Veerox AI", cover_t)
gap(6)
para("Production Execution Plan", cover_s)
gap(30)
para("Multi-Channel Voice + WhatsApp AI Agent", S("cs2", parent=cover_s, fontSize=11,
     textColor=colors.HexColor("#a5b4fc")))
gap(120)
para("Prepared 29 May 2026 &nbsp;&middot;&nbsp; Confidential",
     S("cd", parent=cover_s, fontSize=9, textColor=colors.HexColor("#818cf8")))
story.append(PageBreak())

# ============ 1. OVERVIEW ============
para("1. Executive Summary", h1)
para(
    "<b>Veerox AI</b> is a multi-channel conversational agent. Customers reach the business by "
    "<b>phone call</b> (voice) or <b>WhatsApp message</b>, and an AI agent powered by OpenAI GPT-4o "
    "answers naturally in <b>English or Hindi</b>. It captures leads, books appointments, looks up "
    "existing customers, and escalates to a human when needed. Operators monitor and control "
    "everything from a Next.js admin dashboard — live transcripts, lead inbox, cost tracking, and a "
    "kill-switch to pause the agent instantly.")
gap(4)
para("Architecture principle: <b>one brain, two mouths</b> &mdash; a single channel-agnostic "
     "<font face='Courier'>AgentCore</font> performs all reasoning; voice and WhatsApp are thin "
     "transport adapters around it. This prevents the two channels from drifting apart over time.")

para("Where the project stands today", h2)
mktable([
    ["Component", "Status", "Note"],
    ["Agent brain (GPT-4o + tools + memory)", "DONE", "Fully implemented & tested"],
    ["WhatsApp channel (in/out + voice notes)", "DONE", "Awaiting Meta credentials only"],
    ["Admin dashboard (7 pages)", "DONE", "Polished UI, runs on :3001"],
    ["Database + Redis + migrations", "DONE", "Neon Postgres + Redis"],
    ["Admin API (stats, kill-switch, escalations)", "DONE", "Complete"],
    ["Voice channel (Twilio + Realtime)", "NOT BUILT", "3 stub files — main gap"],
    ["Outbound dial backend", "MISSING", "calls.py to build"],
    ["Cost tracking (pricing constants)", "PARTIAL", "costs.py missing"],
    ["Production deployment + CI/CD", "NOT DONE", "Local only today"],
], [180, 70, 200])
gap(4)
para("<b>Bottom line:</b> the WhatsApp path is production-grade code waiting on credentials. The "
     "<b>voice channel is the main unbuilt feature.</b> Everything else is hardening and deployment.",
     S("note", parent=body, textColor=INDIGO_DK))
story.append(PageBreak())

# ============ 2. STACK ============
para("2. Technology Stack", h1)
mktable([
    ["Layer", "Technology", "Why"],
    ["Backend API", "FastAPI (Python 3.12, async)", "Async-native; ideal for webhooks + WebSockets"],
    ["AI — chat", "OpenAI GPT-4o", "Strong Hindi/English + tool calling"],
    ["AI — voice", "OpenAI Realtime API", "Native speech-to-speech, low latency"],
    ["Transcription", "OpenAI Whisper", "WhatsApp voice notes → text"],
    ["Telephony", "Twilio Voice + Media Streams", "Phone numbers + audio WebSocket"],
    ["WhatsApp", "Meta Cloud API (direct)", "Lower cost, full control"],
    ["Database", "PostgreSQL 16 + SQLAlchemy + Alembic", "Relational, multi-tenant ready"],
    ["Cache / state", "Redis", "Kill-switch, rate limits, idempotency"],
    ["Frontend", "Next.js 14 + Tailwind", "Admin control plane"],
    ["Observability", "structlog (JSON) + Sentry", "Cloud-agnostic logs + errors"],
    ["Deploy", "Docker + Caddy (auto-TLS) on EC2", "Simple single-box production"],
], [85, 175, 190])
story.append(PageBreak())

# ============ 3. EXECUTION ROADMAP ============
para("3. Execution Roadmap", h1)
para("Phased so each phase ends with something demonstrable. Build order: "
     "<b>A &rarr; (B in parallel) &rarr; C &rarr; D</b>.")

para("Phase A &mdash; Finish the feature set &nbsp;(~1 week)", h2)
mktable([
    ["#", "Task", "File(s)"],
    ["A1", "Build voice TwiML webhook", "channels/voice/twilio_webhook.py"],
    ["A2", "Build Realtime audio bridge (WebSocket + interruption via mark/clear)", "channels/voice/realtime_bridge.py"],
    ["A3", "Voice tool-call bridging (reuse same DISPATCH_TABLE)", "channels/voice/adapter.py"],
    ["A4", "Stream voice transcripts to DB during the call", "realtime_bridge.py"],
    ["A5", "Outbound dial endpoint", "routers/calls.py + main.py"],
    ["A6", "Pricing constants + USD-spend calc", "core/costs.py"],
], [22, 270, 158])
gap(3)
para("<b>Acceptance:</b> call the Twilio number &rarr; English/Hindi conversation &rarr; capture_lead "
     "fires &rarr; interrupting mid-sentence stops playback in ~200ms &rarr; transcript rows appear "
     "<i>during</i> the call.", small)

para("Phase B &mdash; Credentials &amp; live integration &nbsp;(parallel, lead-time heavy)", h2)
mktable([
    ["#", "Task"],
    ["B1", "OpenAI org: confirm gpt-4o AND gpt-4o-realtime are enabled"],
    ["B2", "Twilio: buy/port India number (KYC 1–3 days), set Voice webhook"],
    ["B3", "Meta: verify business, create WA app, get permanent token + Phone Number ID"],
    ["B4", "Submit 2 WhatsApp utility templates (24–48h approval; expect 1 rejection)"],
    ["B5", "Register webhooks (WA + Twilio) against a stable HTTPS domain"],
    ["B6", "Fill empty Twilio + Meta keys in .env"],
], [22, 428])

para("Phase C &mdash; Production hardening &nbsp;(~1 week)", h2)
mktable([
    ["#", "Task", "Why"],
    ["C1", "Secrets → AWS SSM Parameter Store", "No secrets on the box"],
    ["C2", "Caddy reverse proxy + auto-TLS", "Meta/Twilio require HTTPS"],
    ["C3", "Enable webhook signature validation (Meta + Twilio)", "Block spoofed requests"],
    ["C4", "Rate limiting per channel + per user phone", "Abuse / cost guard"],
    ["C5", "Graceful LLM-failure fallbacks (chat + Realtime)", "Never crash on 5xx"],
    ["C6", "Idempotency on WhatsApp message IDs", "Meta retries → no double reply"],
    ["C7", "Sentry DSN populated + tested", "Error visibility"],
    ["C8", "uvicorn --workers 2 behind Caddy", "Concurrency (voice WS pins a worker)"],
    ["C9", "Nightly pg_dump → S3 backup", "Disaster recovery"],
    ["C10", "Per-call cost counter (audio seconds) to DB", "Catch stuck sessions in $"],
], [26, 240, 184])
story.append(PageBreak())

para("Phase D &mdash; Ops, CI/CD &amp; observability &nbsp;(~3–4 days)", h2)
mktable([
    ["#", "Task"],
    ["D1", "GitHub Actions: ruff + mypy + pytest on every PR"],
    ["D2", "Docker build + push to registry on merge to main"],
    ["D3", "Deploy step (SSH/compose pull) or watchtower auto-update"],
    ["D4", "Health checks (/health + /ready) wired to uptime monitor"],
    ["D5", "Cost + latency dashboard fed with real data"],
    ["D6", "Ship stdout JSON logs to CloudWatch/Loki"],
    ["D7", "Runbook: rotate keys, pause agent, restore backup"],
], [22, 428])

para("Phase E &mdash; Scale &amp; Phase-2 (later, out of MVP scope)", h2)
para("Multi-tenant org switching &middot; concurrency/load testing &middot; call-recording playback "
     "&middot; knowledge-base RAG (pgvector) &middot; durable jobs (arq/Celery) &middot; real auth "
     "(OAuth/SSO) replacing the static admin token &middot; browser-based voice (LiveKit).", small)
story.append(PageBreak())

# ============ 4. READINESS CHECKLIST ============
para("4. Production-Readiness Checklist", h1)


def checklist(title, items, color):
    para(title, S("clt", parent=h2, fontSize=12, textColor=color))
    rows = [["", it] for it in items]
    t = Table(rows, colWidths=[16, 434])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (1, 0), (1, -1), SLATE),
        ("TEXTCOLOR", (0, 0), (0, -1), color),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    for i in range(len(rows)):
        rows[i][0] = Paragraph("☐", S("box", textColor=color, fontSize=11))
        rows[i][1] = Paragraph(items[i], cell)
    story.append(t)
    gap(6)


checklist("Security", [
    "Secrets in SSM, never in repo or plaintext on disk",
    "Both webhook signatures (Meta + Twilio) validated",
    "Admin token replaced with real auth, or strong rotated token + IP allowlist",
    "Rate limits live on all public endpoints",
    "OpenAI keys rotated (the two exposed during dev MUST be rotated)",
], RED)
checklist("Reliability", [
    "Graceful fallback on every external API failure",
    "Idempotency on inbound webhooks",
    "DB migrations run automatically on deploy",
    "Nightly backups verified by a test restore",
], AMBER)
checklist("Observability", [
    "Structured logs shipping to an aggregator",
    "Sentry capturing errors",
    "Cost + latency visible on dashboard",
    "Uptime monitor on /ready",
], INDIGO)
checklist("Delivery", [
    "CI green (lint + types + tests) required to merge",
    "One-command deploy",
    "Rollback path documented",
], GREEN)
story.append(PageBreak())

# ============ 5. RISKS ============
para("5. Risks &amp; Mitigations", h1)
mktable([
    ["Risk", "Impact", "Mitigation"],
    ["OpenAI Realtime cost (~$1.50 / 5-min call)", "Budget blow", "Per-call $ counter + kill-switch + alerts"],
    ["Meta template rejection", "Launch delay", "Submit Day 1; expect 1 rejection cycle"],
    ["Twilio↔OpenAI audio format mismatch", "Garbled audio", "Use g711_ulaw both sides (no resampling)"],
    ["Webhook >15s → Meta retries", "Duplicate replies", "Fast-ACK + background task (done for WA)"],
    ["Ephemeral voice transcripts", "Data loss", "Stream to DB during call (Phase A4)"],
    ["Single EC2 box", "Single point of failure", "Phase-2 ALB + 2 instances; monitor + backup for MVP"],
], [165, 105, 185])
gap(14)
para("Build order: <b>A &rarr; (B in parallel) &rarr; C &rarr; D</b>. This document is the source of "
     "truth for taking Veerox AI from “WhatsApp working locally” to “voice + WhatsApp "
     "running in production.”", S("foot", parent=small, textColor=INDIGO_DK))


# ----- footer on content pages -----
def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
    canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(SLATE_LT)
    canvas.drawString(18 * mm, 10 * mm, "Veerox AI — Production Execution Plan")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, "Page %d" % doc.page)
    canvas.restoreState()


doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=18 * mm, rightMargin=18 * mm,
                        topMargin=18 * mm, bottomMargin=20 * mm,
                        title="Veerox AI — Production Execution Plan",
                        author="Veerox")
doc.build(story, onFirstPage=cover, onLaterPages=footer)
print("WROTE", OUT)
