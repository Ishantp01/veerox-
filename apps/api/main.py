from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from apps.api.channels.voice import plivo_client
from apps.api.channels.voice.realtime_bridge import router as voice_stream_router
from apps.api.channels.voice.webhook import router as voice_router
from apps.api.channels.whatsapp.webhook import router as whatsapp_router
from apps.api.config import settings
from apps.api.logging import setup_logging
from apps.api.rate_limit import limiter
from apps.api.routers import (
    admin,
    appointments,
    auth,
    billing,
    conversations,
    crm,
    diag,
    follow_ups,
    health,
    helpdesk,
    leads,
    sales,
    team,
    templates,
)
from apps.api.sentry import init_sentry
from apps.api.workers.campaign_dialer import run_campaign_dialer
from apps.api.workers.follow_up_dispatcher import run_follow_up_dispatcher
from apps.api.workers.whatsapp_dispatcher import run_whatsapp_dispatcher


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    init_sentry()
    plivo_registration_task = asyncio.create_task(plivo_client.register_inbound_answer_url())
    dialer_task = asyncio.create_task(run_campaign_dialer())
    whatsapp_dispatcher_task = asyncio.create_task(run_whatsapp_dispatcher())
    follow_up_task = asyncio.create_task(run_follow_up_dispatcher())
    # billing_expiry_task removed from here — plans no longer expire on a
    # timer, see removefeature.md §4 to re-add.
    background_tasks = (
        plivo_registration_task,
        dialer_task,
        whatsapp_dispatcher_task,
        follow_up_task,
    )
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="Veerox AI",
        description="Voice + WhatsApp AI agent backend.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Read allowed CORS origins from settings (env-driven). Set
    # CORS_ALLOWED_ORIGINS in Render to include the deployed frontend URL,
    # e.g. "https://veerox-web.vercel.app,http://localhost:3001".
    allowed_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Token", "X-Admin-Token"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(billing.router)
    app.include_router(conversations.router)
    app.include_router(leads.router)
    app.include_router(crm.router)
    app.include_router(appointments.router)
    app.include_router(follow_ups.router)
    app.include_router(templates.router)
    app.include_router(sales.router)
    app.include_router(team.router)
    app.include_router(admin.router)
    app.include_router(helpdesk.router)
    app.include_router(diag.router)
    app.include_router(whatsapp_router)
    app.include_router(voice_router)
    app.include_router(voice_stream_router)

    return app


app = create_app()
