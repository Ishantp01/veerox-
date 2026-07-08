from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "dev",
    pool_pre_ping=True,
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
