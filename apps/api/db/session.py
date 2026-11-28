from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
_pool_kwargs: dict = {}
if not _is_sqlite:
    # Sized for the production Postgres (RDS, direct connections, no PgBouncer): the pool can never ask for more
    # connections than the server allows (max_connections is ~100 on a small RDS), so a traffic spike queues
    # briefly instead of failing with "too many clients". Override with DB_POOL_SIZE / DB_MAX_OVERFLOW.
    _pool_kwargs = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": 30,
        # RDS (and any NAT/firewall between us) silently drops idle connections; pre-ping makes a dead pooled
        # connection reconnect transparently instead of failing the first request that picks it up.
        "pool_pre_ping": True,
    }

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_recycle=270,
    **_pool_kwargs,
    # Harmless on direct Postgres and required if a transaction-mode pooler (PgBouncer/Neon) is ever put back.
    connect_args={"statement_cache_size": 0} if not _is_sqlite else {},
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
