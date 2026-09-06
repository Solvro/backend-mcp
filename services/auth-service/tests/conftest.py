from typing import AsyncGenerator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from auth_app.main import app
from auth_app.settings import get_settings
from common.db import Base, get_session
from common.redis import get_redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def setup_test_settings(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-test-key-1234567890123456")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    yield

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def redis_client() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    fake_redis = fakeredis.aioredis.FakeRedis()
    yield fake_redis
    await fake_redis.aclose()


@pytest_asyncio.fixture(scope="function")
async def async_client(
    db_session: AsyncSession,
    redis_client: fakeredis.aioredis.FakeRedis,
) -> AsyncGenerator[AsyncClient, None]:
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_redis] = lambda: redis_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def client(async_client: AsyncClient) -> AsyncClient:
    return async_client
