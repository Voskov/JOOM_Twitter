from __future__ import annotations

import os

import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.database import get_db
from app.db.models import Base
from app.db.redis_client import get_redis_pool
from app.main import app

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/joon_twitter_test",
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")
RATE_LIMIT_REDIS_URL = "redis://localhost:6379/2"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def disable_rate_limiting():
    """Disable rate limiting globally for all tests except test_rate_limit.py.

    The rate_limit_client fixture re-enables it for those specific tests.
    Without this, tests sharing the same IP (127.0.0.1) accumulate signin
    hits across the session and trip the 5/min limit.
    """
    from app.core.rate_limit import limiter as prod_limiter

    prod_limiter.enabled = False
    yield
    prod_limiter.enabled = True


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def redis_client():
    client: aioredis.Redis = aioredis.from_url(
        TEST_REDIS_URL, encoding="utf-8", decode_responses=True
    )
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def client(test_engine, redis_client):
    async def override_get_db():
        session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session

    async def override_get_redis_pool() -> aioredis.Redis:
        return redis_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_pool] = override_get_redis_pool

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def rate_limit_client(test_engine, redis_client):
    from limits.storage import storage_from_string

    from app.core.rate_limit import limiter as prod_limiter

    # Re-enable rate limiting (disabled globally by disable_rate_limiting fixture)
    # and redirect counters to an isolated DB 2 so tests don't bleed into each other.
    test_storage = storage_from_string(RATE_LIMIT_REDIS_URL)
    original_storage = prod_limiter._storage
    original_limiter_storage = prod_limiter._limiter.storage
    prod_limiter._storage = test_storage
    prod_limiter._limiter.storage = test_storage
    prod_limiter.enabled = True

    rl_redis = aioredis.from_url(RATE_LIMIT_REDIS_URL)
    await rl_redis.flushdb()

    async def override_get_db():
        session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session

    async def override_get_redis_pool() -> aioredis.Redis:
        return redis_client

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis_pool] = override_get_redis_pool

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    prod_limiter.enabled = False
    prod_limiter._storage = original_storage
    prod_limiter._limiter.storage = original_limiter_storage
    await rl_redis.flushdb()
    await rl_redis.aclose()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient):
    """AsyncClient with a pre-registered and signed-in user, with auth header set."""
    await client.post("/auth/signup", json={"username": "testuser", "password": "password123"})
    resp = await client.post(
        "/auth/signin", json={"username": "testuser", "password": "password123"}
    )
    token = resp.json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client
