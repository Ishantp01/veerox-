from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.db.base import Base
from apps.api.deps import get_db
from apps.api.routers import auth as auth_router


class FakeRedis:
    """In-process Redis stand-in covering the subset of commands exercised
    by session storage (apps/api/core/sessions.py) and admin's error/queue
    reads — shared across test modules so auth/billing tests don't each
    reimplement it.
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.expiry: dict[str, float] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    def _expired(self, key: str) -> bool:
        exp = self.expiry.get(key)
        return exp is not None and exp < time.monotonic()

    async def get(self, key: str) -> str | None:
        if self._expired(key):
            self.kv.pop(key, None)
            return None
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value
        if ex is not None:
            self.expiry[key] = time.monotonic() + ex

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.kv.pop(key, None)
            self.expiry.pop(key, None)
            self.lists.pop(key, None)
            self.sets.pop(key, None)

    async def sadd(self, key: str, *values: str) -> None:
        self.sets.setdefault(key, set()).update(values)

    async def srem(self, key: str, *values: str) -> None:
        self.sets.get(key, set()).difference_update(values)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)


class FakePipeline:
    """Queues (method_name, args, kwargs) calls against the owning FakeRedis
    and runs them in order on `execute()` — enough to stand in for
    redis-py's pipeline as an async context manager (see
    core/sessions.py's create_session)."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._queue: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def _queue_call(*args, **kwargs):
            self._queue.append((name, args, kwargs))
            return self

        return _queue_call

    async def execute(self) -> list:
        results = []
        for name, args, kwargs in self._queue:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        self._queue.clear()
        return results

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def test_engine():
    """Function-scoped engine + fresh schema per test.

    With ``sqlite+aiosqlite:///:memory:`` each new engine gets its own
    isolated in-memory database — perfect test isolation, no fixture
    cross-talk on shared seed data (e.g. the demo Org with a fixed UUID).
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest_asyncio.fixture
async def client(
    db_session: AsyncSession, fake_redis: FakeRedis, test_engine, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[AsyncClient, None]:
    from apps.api.deps import get_redis_dep
    from apps.api.main import create_app

    app = create_app()

    # /auth/login records last_login_at (and the admin-token bootstrap path
    # persists the default org owner) via its own AsyncSessionLocal() call
    # rather than the request's `db` — same pattern/reasoning as
    # campaign_dialer, follow_up_dispatcher, etc. below — so it needs the
    # same test-engine redirect those already get.
    monkeypatch.setattr(
        auth_router, "AsyncSessionLocal", async_sessionmaker(bind=test_engine, expire_on_commit=False)
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> AsyncGenerator[FakeRedis, None]:
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_dep] = override_get_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
