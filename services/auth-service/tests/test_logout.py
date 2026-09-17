import jwt
import pytest
from auth_app.models import RefreshToken, Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from common.auth import require_auth
from common.errors import AuthError
from common.redis import denylist_key
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

pytestmark = pytest.mark.unit

PASSWORD = "SecretPassword123!"


async def _login(
    client: AsyncClient, db: AsyncSession, *, email: str = "alice@example.com"
) -> dict:
    user = User(
        username=email.split("@")[0],
        email=email,
        password_hash=get_password_manager().hash_password(PASSWORD),
        email_verified=True,
        roles=[Role(name=f"role-{email}")],
    )
    db.add(user)
    await db.commit()
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


@pytest.mark.asyncio
async def test_logout_denylists_access_token_until_its_expiry(
    async_client, db_session, redis_client
) -> None:
    tokens = await _login(async_client, db_session)
    settings = get_settings()
    jti = jwt.decode(tokens["access_token"], options={"verify_signature": False})["jti"]

    resp = await async_client.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=_bearer(tokens["access_token"]),
    )

    assert resp.status_code == 204
    ttl = await redis_client.ttl(denylist_key(jti, settings=settings))
    expected = settings.access_token_expire_minutes * 60 + settings.jwt_leeway_seconds
    assert expected - 5 <= ttl <= expected


@pytest.mark.asyncio
async def test_logged_out_access_token_is_rejected_by_require_auth(
    async_client, db_session
) -> None:
    tokens = await _login(async_client, db_session)
    settings = get_settings()
    guard = require_auth(settings=settings)

    assert await guard(_request(_bearer(tokens["access_token"]))) is not None

    await async_client.post("/auth/logout", json={}, headers=_bearer(tokens["access_token"]))

    with pytest.raises(AuthError, match="revoked"):
        await guard(_request(_bearer(tokens["access_token"])))


@pytest.mark.asyncio
async def test_logout_revokes_the_whole_refresh_family(async_client, db_session) -> None:
    first = await _login(async_client, db_session)
    second = (
        await async_client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).json()

    resp = await async_client.post(
        "/auth/logout",
        json={"refresh_token": second["refresh_token"]},
        headers=_bearer(second["access_token"]),
    )
    assert resp.status_code == 204

    rows = list((await db_session.execute(select(RefreshToken))).scalars())
    assert len(rows) == 2
    for row in rows:
        await db_session.refresh(row)
        assert row.revoked is True

    again = await async_client.post(
        "/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert again.status_code == 401


@pytest.mark.asyncio
async def test_logout_without_bearer_is_401(async_client) -> None:
    resp = await async_client.post("/auth/logout", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_ignores_another_users_refresh_token(async_client, db_session) -> None:
    alice = await _login(async_client, db_session, email="alice@example.com")
    bob = await _login(async_client, db_session, email="bob@example.com")

    resp = await async_client.post(
        "/auth/logout",
        json={"refresh_token": bob["refresh_token"]},
        headers=_bearer(alice["access_token"]),
    )
    assert resp.status_code == 204  # no oracle

    bob_still_ok = await async_client.post(
        "/auth/refresh", json={"refresh_token": bob["refresh_token"]}
    )
    assert bob_still_ok.status_code == 200


@pytest.mark.asyncio
async def test_logout_is_idempotent(async_client, db_session) -> None:
    tokens = await _login(async_client, db_session)
    body = {"refresh_token": tokens["refresh_token"]}
    headers = _bearer(tokens["access_token"])

    assert (await async_client.post("/auth/logout", json=body, headers=headers)).status_code == 204
    assert (await async_client.post("/auth/logout", json=body, headers=headers)).status_code == 204
