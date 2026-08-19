from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    # NOT pool_pre_ping: measured it forcing a full reconnect (TLS + auth
    # handshake, ~1.5-1.9s against Neon) on nearly every checkout instead of
    # reusing the pooled connection — turned every request's first query
    # into the slowest part of the page. pool_recycle is the safety net
    # instead: proactively refresh connections on a timer rather than
    # pinging (and re-paying a round trip for) every single checkout.
    pool_recycle=270,
    # SQLAlchemy defaults (5 + 10 overflow) get pinched under concurrent
    # dashboard polling (multiple open tabs, several 3-4s intervals each) —
    # and more importantly under many simultaneous voice calls, each of
    # which opens a short-lived AsyncSessionLocal() for every tool call,
    # transcript persist, and a usage-limit check every 20s
    # (realtime_bridge.py::_watch_usage_limit). At 100 concurrent calls
    # that's a real, recurring burst of checkouts, not just idle
    # dashboard tabs. Neon's pooled endpoint fronts this with PgBouncer in
    # transaction-pooling mode, so raising the app-side pool doesn't
    # multiply real Postgres connections 1:1 — but Neon's own PgBouncer
    # pool has its own ceiling too; confirm it can actually take this many
    # before assuming headroom exists.
    #
    # Load-tested 2026-08-19 (see longrunning/operations/load.md):
    #   pool_size=30/overflow=50  (80 total)  -> clean to ~700, fails from 800
    #   pool_size=100/overflow=150 (250 total) -> clean to ~1200, fails from 1200+
    # Raising further to push the ceiling again. NOTE: this number is not
    # itself load-tested yet, and there's a real ceiling this approach can't
    # get past — Neon's PgBouncer has its own connection limit (unknown from
    # here, tied to the Neon plan), and one single-process app instance has
    # a real CPU/memory limit regardless of pool size. For genuinely
    # thousands of concurrent calls, this needs horizontal scaling (multiple
    # app instances) and a Neon plan sized for it, not just a bigger number
    # here — see longrunning/operations/load.md.
    pool_size=250,
    max_overflow=350,
    # Neon's pooled endpoint runs PgBouncer in transaction-pooling mode, which
    # is incompatible with asyncpg's server-side prepared statement cache.
    connect_args={"statement_cache_size": 0},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
