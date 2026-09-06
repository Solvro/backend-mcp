import pytest
from auth_app.models import User
from auth_app.verification import create_and_store_token, hash_token
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_verify_email_success(
    async_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = User(
        username="bob",
        email="bob@example.com",
        password_hash="hashed_password",
        is_active=True,
        email_verified=False,
        verified_at=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    raw_token = "valid-test-token-123"

    hashed = hash_token(raw_token)
    await redis_client.set(f"emailverify:{hashed}", str(user.id), ex=3600)

    response = await async_client.get(f"/auth/verify?token={raw_token}")

    assert response.status_code == 200
    assert "verified" in response.json().get("message", "").lower()

    result = await db_session.execute(select(User).where(User.id == user.id))
    updated_user = result.scalar_one()

    assert updated_user.email_verified is True
    assert updated_user.verified_at is not None

    token_in_redis = await redis_client.get(f"email_verify:{raw_token}")
    assert token_in_redis is None


@pytest.mark.asyncio
async def test_verify_email_invalid_token(client: AsyncClient):
    response = await client.get("/auth/verify?token=invalidtoken")
    assert response.status_code == 400
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_verify_email_user_not_found(
    async_client: AsyncClient,
    redis_client: Redis,
) -> None:
    non_existent_user_id = 999999
    raw_token = "orphaned-token-123"
    await redis_client.set(
        f"emailverify:{hash_token(raw_token)}", str(non_existent_user_id), ex=3600
    )
    response = await async_client.get(f"/auth/verify?token={raw_token}")

    assert response.status_code == 400
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_verify_email_token_reuse_fails(
    async_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = User(
        username="reuse_user", email="reuse@example.com", password_hash="hash", email_verified=False
    )
    db_session.add(user)
    await db_session.commit()

    raw_token = "single-use-token-123"
    await redis_client.set(f"emailverify:{hash_token(raw_token)}", str(user.id), ex=3600)

    first_res = await async_client.get(f"/auth/verify?token={raw_token}")
    assert first_res.status_code == 200

    second_res = await async_client.get(f"/auth/verify?token={raw_token}")
    assert second_res.status_code == 400
    assert second_res.json().get("detail") == "invalid_or_expired_token"


@pytest.mark.asyncio
async def test_raw_token_never_logged(
    async_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_token = "super-secret-raw-token-999"

    await async_client.get(f"/auth/verify?token={raw_token}")

    app_log_messages = [
        record.getMessage() for record in caplog.records if record.name.startswith("auth_app")
    ]

    for message in app_log_messages:
        assert raw_token not in message


@pytest.mark.asyncio
async def test_verify_email_token_cannot_be_reused(
    async_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:
    user = User(
        username="reuse_user",
        email="reuse@example.com",
        password_hash="hash123",
        email_verified=False,
    )
    db_session.add(user)
    await db_session.commit()

    raw_token = await create_and_store_token(redis_client, user.id)

    first_res = await async_client.get(f"/auth/verify?token={raw_token}")
    assert first_res.status_code == 200

    second_res = await async_client.get(f"/auth/verify?token={raw_token}")
    assert second_res.status_code == 400
    assert second_res.json().get("detail") == "invalid_or_expired_token"
