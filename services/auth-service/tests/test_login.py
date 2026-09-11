import pytest
from auth_app.models import User
from auth_app.security import get_password_manager
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_login_success(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    pm = get_password_manager()
    raw_password = "SecretPassword123!"
    hashed_password = pm.hash_password(raw_password)

    user = User(
        username="verified_alice",
        email="alice@example.com",
        password_hash=hashed_password,
        is_active=True,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    payload = {
        "email": "alice@example.com",
        "password": raw_password,
    }
    response = await async_client.post("/auth/login", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"


@pytest.mark.asyncio
async def test_login_unverified_email(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    pm = get_password_manager()
    unverified_user = User(
        username="unverified_john",
        email="john@example.com",
        password_hash=pm.hash_password("Password123!"),
        email_verified=False,
    )
    db_session.add(unverified_user)
    await db_session.commit()

    payload = {
        "email": "john@example.com",
        "password": "Password123!",
    }

    response = await async_client.post("/auth/login", json=payload)

    assert response.status_code == 403
    assert response.json().get("detail") == "email_unverified"


@pytest.mark.asyncio
async def test_login_invalid_credentials(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    raw_password = "SecretPassword123!"
    pm = get_password_manager()
    user = User(
        username="charlie",
        email="charlie@example.com",
        password_hash=pm.hash_password(raw_password),
        is_active=True,
        email_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    wrong_pwd_res = await async_client.post(
        "/auth/login",
        json={"email": "charlie@example.com", "password": "WrongPassword123!"},
    )
    assert wrong_pwd_res.status_code == 400

    wrong_email_res = await async_client.post(
        "/auth/login",
        json={"email": "nonexistent@example.com", "password": raw_password},
    )
    assert wrong_email_res.status_code == 400
