import pytest
from auth_app.models import RefreshToken, Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from common.auth import decode_access_token
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

PASSWORD = "SecretPassword123!"


async def _login(
    client: AsyncClient, db: AsyncSession, *, email: str = "alice@example.com"
) -> dict:
    user_role = (await db.execute(select(Role).where(Role.name == "user"))).scalar_one()
    user = User(
        username=email.split("@")[0],
        email=email,
        password_hash=get_password_manager().hash_password(PASSWORD),
        email_verified=True,
        roles=[user_role],
    )
    db.add(user)
    await db.commit()
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()


async def _rows(db: AsyncSession) -> list[RefreshToken]:
    return list((await db.execute(select(RefreshToken).order_by(RefreshToken.id))).scalars())


@pytest.mark.asyncio
async def test_valid_refresh_rotates_and_issues_new_pair(async_client, db_session) -> None:
    first = await _login(async_client, db_session)

    resp = await async_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})

    assert resp.status_code == 200
    second = resp.json()
    assert second["token_type"] == "bearer"
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    claims = decode_access_token(second["access_token"], get_settings())
    assert claims["roles"] == ["user"]

    old, new = await _rows(db_session)
    assert old.revoked is True
    assert new.revoked is False
    assert new.family_id == old.family_id == old.jti


@pytest.mark.asyncio
async def test_replayed_refresh_token_is_rejected_and_kills_the_family(
    async_client, db_session
) -> None:
    first = await _login(async_client, db_session)
    second = (
        await async_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    replay = await async_client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401
    assert replay.json()["detail"] == "invalid_refresh_token"

    # the legitimately rotated token is now dead too
    after = await async_client.post(
        "/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert after.status_code == 401

    for row in await _rows(db_session):
        await db_session.refresh(row)
        assert row.revoked is True


@pytest.mark.asyncio
async def test_reuse_only_kills_its_own_family(async_client, db_session) -> None:
    phone = await _login(async_client, db_session)
    laptop = (
        await async_client.post(
            "/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
        )
    ).json()

    rotated = await async_client.post(
        "/auth/refresh", json={"refresh_token": phone["refresh_token"]}
    )
    assert rotated.status_code == 200
    replay = await async_client.post(
        "/auth/refresh", json={"refresh_token": phone["refresh_token"]}
    )
    assert replay.status_code == 401

    still_ok = await async_client.post(
        "/auth/refresh", json={"refresh_token": laptop["refresh_token"]}
    )
    assert still_ok.status_code == 200


@pytest.mark.asyncio
async def test_access_token_is_not_accepted_as_refresh_token(async_client, db_session) -> None:
    tokens = await _login(async_client, db_session)

    resp = await async_client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_refresh_token"


@pytest.mark.asyncio
async def test_garbage_refresh_token_is_rejected(async_client) -> None:
    resp = await async_client.post("/auth/refresh", json={"refresh_token": "not.a.jwt"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_refresh_token"


@pytest.mark.asyncio
async def test_refresh_for_deactivated_user_is_rejected(async_client, db_session) -> None:
    tokens = await _login(async_client, db_session)
    user = (await db_session.execute(select(User))).scalar_one()
    user.is_active = False
    await db_session.commit()

    resp = await async_client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_survives_key_rotation(async_client, db_session, monkeypatch) -> None:
    import jwt
    from common.jwt_keys import key_id
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    issued_under_a = await _login(async_client, db_session)
    key_a_public = get_settings().jwt_public_key

    key_b = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv(
        "JWT_PRIVATE_KEY",
        key_b.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    key_b_public = (
        key_b.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    monkeypatch.setenv("JWT_PUBLIC_KEY", key_b_public)
    monkeypatch.setenv("JWT_PREVIOUS_PUBLIC_KEY", key_a_public)
    get_settings.cache_clear()

    resp = await async_client.post(
        "/auth/refresh", json={"refresh_token": issued_under_a["refresh_token"]}
    )

    assert resp.status_code == 200
    new_access = resp.json()["access_token"]
    assert jwt.get_unverified_header(new_access)["kid"] == key_id(key_b_public)
    assert decode_access_token(new_access, get_settings())["sub"]
    assert decode_access_token(issued_under_a["access_token"], get_settings())["sub"]
