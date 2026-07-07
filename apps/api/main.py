from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from apps.api.channels.voice.realtime_bridge import router as voice_stream_router
from apps.api.channels.voice.webhook import router as voice_router
from apps.api.channels.whatsapp.webhook import router as whatsapp_router
from apps.api.config import settings
from apps.api.logging import setup_logging
from apps.api.rate_limit import limiter
from apps.api.routers import admin, conversations, diag, health, leads
from apps.api.sentry import init_sentry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    init_sentry()
    yield


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
    # e.g. "https://veerox-web.vercel.app,http://localhost:3000".
    allowed_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(leads.router)
    app.include_router(admin.router)
    app.include_router(diag.router)
    app.include_router(whatsapp_router)
    app.include_router(voice_router)
    app.include_router(voice_stream_router)

    return app


app = create_app()
