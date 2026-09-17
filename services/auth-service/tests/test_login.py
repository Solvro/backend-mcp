import time

import jwt
import pytest
from auth_app.models import RefreshToken, Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from auth_app.verification import hash_token
from common.jwt_keys import key_id
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_login_success(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    settings = get_settings()
    pm = get_password_manager()
    raw_password = "SecretPassword123!"
    hashed_password = pm.hash_password(raw_password)

    user_role = (await db_session.execute(select(Role).where(Role.name == "user"))).scalar_one()

    user = User(
        username="verified_alice",
        email="alice@example.com",
        password_hash=hashed_password,
        is_active=True,
        email_verified=True,
        roles=[user_role],
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user, attribute_names=["roles"])

    payload = {
        "email": "alice@example.com",
        "password": raw_password,
    }
    response = await async_client.post("/auth/login", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data.get("token_type") == "bearer"

    access_token = data["access_token"]
    decoded_access = jwt.decode(
        access_token,
        settings.jwt_public_key,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
    )
    assert decoded_access["sub"] == str(user.id)
    assert decoded_access["roles"] == ["user"]
    assert decoded_access["email_verified"] is True
    assert decoded_access["iss"] == settings.jwt_issuer
    assert decoded_access["aud"] == settings.jwt_audience
    assert "exp" in decoded_access
    assert "jti" in decoded_access
    assert decoded_access["typ"] == "access"
    assert abs(decoded_access["iat"] - time.time()) < 5
    assert isinstance(decoded_access["iat"], float)  # sub-second, see revoke_user_tokens
    assert jwt.get_unverified_header(access_token)["kid"] == key_id(settings.jwt_public_key)

    refresh_token = data["refresh_token"]
    hashed_refresh_token = hash_token(refresh_token)

    stmt = select(RefreshToken).where(RefreshToken.token_hash == hashed_refresh_token)
    result = await db_session.execute(stmt)
    db_token = result.scalar_one_or_none()

    assert db_token is not None
    assert db_token.user_id == user.id
    assert db_token.family_id == db_token.jti  # login starts a new rotation family

    decoded_refresh = jwt.decode(
        refresh_token,
        settings.jwt_public_key,
        algorithms=[settings.jwt_algorithm],
        audience=settings.jwt_audience,
    )

    assert decoded_refresh["sub"] == str(user.id)
    assert decoded_refresh["roles"] == ["user"]
    assert decoded_refresh["email_verified"] is True
    assert decoded_refresh["iss"] == settings.jwt_issuer
    assert decoded_refresh["aud"] == settings.jwt_audience
    assert "exp" in decoded_refresh
    assert "jti" in decoded_refresh
    assert decoded_refresh["typ"] == "refresh"
    assert abs(decoded_refresh["iat"] - time.time()) < 5
    assert jwt.get_unverified_header(refresh_token)["kid"] == key_id(settings.jwt_public_key)
    assert db_token.jti == decoded_refresh["jti"]


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
    data = response.json()
    assert data.get("detail") == "email_unverified"
    assert "access_token" not in data


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
    assert wrong_pwd_res.status_code == 401
    assert wrong_pwd_res.json().get("detail") == "invalid_credentials"

    wrong_email_res = await async_client.post(
        "/auth/login",
        json={"email": "nonexistent@example.com", "password": raw_password},
    )
    assert wrong_email_res.status_code == 401
    assert wrong_email_res.json().get("detail") == "invalid_credentials"
