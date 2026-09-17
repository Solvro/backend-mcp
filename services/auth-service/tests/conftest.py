from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio
from auth_app.api.auth import (
    change_password_limiter,
    forgot_password_limiter,
    login_limiter,
    refresh_limiter,
    register_limiter,
    resend_limiter,
    reset_password_limiter,
)
from auth_app.main import app
from auth_app.models import DEFAULT_ROLES, Role
from auth_app.settings import get_settings
from common.db import Base, get_session
from common.redis import redis_dependency
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def setup_test_settings(monkeypatch):
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    pem_public = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "super-secret-test-key-1234567890123456")
    monkeypatch.setenv("JWT_PRIVATE_KEY", pem_private)
    monkeypatch.setenv("JWT_PUBLIC_KEY", pem_public)
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_ISSUER", "auth-service")
    monkeypatch.setenv("JWT_AUDIENCE", "auth-api")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

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
        session.add_all(Role(name=n, description=d) for n, d in DEFAULT_ROLES.items())
        await session.commit()
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
    async def no_rate_limit():
        return None

    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[redis_dependency] = lambda: redis_client
    app.dependency_overrides[register_limiter] = no_rate_limit
    app.dependency_overrides[login_limiter] = no_rate_limit
    app.dependency_overrides[resend_limiter] = no_rate_limit
    app.dependency_overrides[refresh_limiter] = no_rate_limit
    app.dependency_overrides[forgot_password_limiter] = no_rate_limit
    app.dependency_overrides[reset_password_limiter] = no_rate_limit
    app.dependency_overrides[change_password_limiter] = no_rate_limit

    with patch("common.redis.get_redis", return_value=redis_client):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client

    app.dependency_overrides.clear()


@pytest.fixture
def client(async_client: AsyncClient) -> AsyncClient:
    return async_client


@pytest.fixture
def mock_email_sender():
    with patch("auth_app.api.auth.send_template_email", new_callable=AsyncMock) as mock_send:
        yield mock_send
