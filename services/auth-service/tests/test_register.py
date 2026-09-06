from unittest.mock import patch

import pytest
from auth_app.models import User
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
@patch("auth_app.api.auth.send_verification_email")
async def test_register_user_success(
    mock_send_email,
    async_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Redis,
) -> None:

    payload = {
        "data": {
            "username": "alice1",
            "email": "alice1@example.com",
            "password": "SecurePassword123!",
        }
    }

    response = await async_client.post("/auth/register", json=payload)

    assert response.status_code == 201
    mock_send_email.assert_called_once()
    assert mock_send_email.call_args[0][0] == "alice1@example.com"

    res_data = response.json()
    assert "id" in res_data
    assert res_data["username"] == "alice1"
    assert res_data["email"] == "alice1@example.com"
    assert res_data["email_verified"] is False
    assert res_data["roles"] == ["user"]
    assert "password" not in res_data
    assert "password_hash" not in res_data

    user_data = payload["data"]
    result = await db_session.execute(select(User).where(User.email == user_data["email"]))
    user = result.scalar_one_or_none()

    assert user is not None
    assert user.username == "alice1"
    assert user.email == "alice1@example.com"
    assert user.email_verified is False
    assert user.verified_at is None
    assert user.password_hash != user_data["password"]

    redis_keys = await redis_client.keys("emailverify:*")
    assert len(redis_keys) == 1


@pytest.mark.asyncio
async def test_register_duplicate_email_or_username(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    existing_user = User(
        username="alice2",
        email="alice2@example.com",
        password_hash="somehash123",
        email_verified=False,
    )
    db_session.add(existing_user)
    await db_session.commit()

    payload = {
        "data": {
            "username": "alice2",
            "email": "alice2@example.com",
            "password": "SecurePassword123!",
        }
    }
    response = await async_client.post("/auth/register", json=payload)

    assert response.status_code in (400, 409)
    assert "detail" in response.json()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"data": {"username": "a", "email": "invalid-email", "password": "123"}},
        {"data": {"username": "", "email": "test@example.com", "password": "ValidPassword123!"}},
        {"data": {"email": "test@example.com", "password": "ValidPassword123!"}},
    ],
)
@pytest.mark.asyncio
async def test_register_invalid_input_validation(
    async_client: AsyncClient,
    invalid_payload: dict[str, dict],
) -> None:
    response = await async_client.post("/auth/register", json=invalid_payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_data",
    [
        {"username": "al", "email": "inv_email1@example.com", "password": "SecurePassword123!"},
        {"username": "alice_valid", "email": "not-an-email", "password": "SecurePassword123!"},
        {"username": "alice_valid2", "email": "inv_email2@example.com", "password": "123"},
    ],
)
@pytest.mark.asyncio
@patch("auth_app.api.auth.send_verification_email")
async def test_register_invalid_fields_inside_data(
    mock_send_email,
    async_client: AsyncClient,
    invalid_data: dict[str, str],
) -> None:
    payload = {"data": invalid_data}
    response = await async_client.post("/auth/register", json=payload)
    assert response.status_code in (400, 422)


@pytest.mark.asyncio
@patch("auth_app.api.auth.send_verification_email", side_effect=Exception("SMTP failure"))
async def test_register_email_send_failure_does_not_fail_registration(
    mock_send_email,
    async_client: AsyncClient,
) -> None:
    payload = {
        "data": {
            "username": "robust_user",
            "email": "robust@example.com",
            "password": "SecurePassword123!",
        }
    }
    response = await async_client.post("/auth/register", json=payload)
    assert response.status_code == 201
