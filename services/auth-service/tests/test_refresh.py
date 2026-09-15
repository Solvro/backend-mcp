import pytest
from auth_app.models import RefreshToken, Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from common.auth import decode_access_token
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "SecretPassword123!"


async def _login(
    client: AsyncClient, db: AsyncSession, *, email: str = "alice@example.com"
) -> dict:
    user = User(
        username=email.split("@")[0],
        email=email,
        password_hash=get_password_manager().hash_password(PASSWORD),
        email_verified=True,
        roles=[Role(name="user")],
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
