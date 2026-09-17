from unittest.mock import AsyncMock, patch

import pytest
from auth_app.models import Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from common.auth import require_auth
from common.email import render_template
from common.errors import AuthError
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

pytestmark = pytest.mark.unit

OLD = "OldPassword123!"
NEW = "BrandNewPassword456!"
EMAIL = "alice@example.com"


async def _user(db: AsyncSession) -> User:
    role = (await db.execute(select(Role).where(Role.name == "user"))).scalar_one()
    user = User(
        username="alice",
        email=EMAIL,
        password_hash=get_password_manager().hash_password(OLD),
        email_verified=True,
        roles=[role],
    )
    db.add(user)
    await db.commit()
    return user


async def _login(client: AsyncClient, password: str = OLD) -> dict:
    resp = await client.post("/auth/login", json={"email": EMAIL, "password": password})
    assert resp.status_code == 200
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


async def _change(client: AsyncClient, access: str, current: str = OLD, new: str = NEW):
    with patch("auth_app.api.auth.send_template_email", new_callable=AsyncMock) as send:
        resp = await client.post(
            "/auth/change-password",
            json={"current_password": current, "new_password": new},
            headers=_bearer(access),
        )
    return resp, send


@pytest.mark.asyncio
async def test_anonymous_is_401(async_client) -> None:
    resp = await async_client.post(
        "/auth/change-password", json={"current_password": OLD, "new_password": NEW}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logged_out_token_cannot_change_password(async_client, db_session) -> None:
    await _user(db_session)
    tokens = await _login(async_client)
    await async_client.post("/auth/logout", json={}, headers=_bearer(tokens["access_token"]))

    resp, _ = await _change(async_client, tokens["access_token"])

    assert resp.status_code == 401
    assert (await _login(async_client, OLD))["access_token"]  # nothing changed


@pytest.mark.asyncio
async def test_wrong_current_password_is_401_and_changes_nothing(async_client, db_session) -> None:
    user = await _user(db_session)
    tokens = await _login(async_client)
    hash_before = user.password_hash

    resp, send = await _change(async_client, tokens["access_token"], current="Nope123!")

    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    await db_session.refresh(user)
    assert user.password_hash == hash_before
    send.assert_not_called()


@pytest.mark.asyncio
async def test_weak_new_password_is_422(async_client, db_session) -> None:
    await _user(db_session)
    tokens = await _login(async_client)

    resp, _ = await _change(async_client, tokens["access_token"], new="short")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unchanged_password_is_422(async_client, db_session) -> None:
    await _user(db_session)
    tokens = await _login(async_client)

    resp, _ = await _change(async_client, tokens["access_token"], current=OLD, new=OLD)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_over_long_new_password_is_400(async_client, db_session) -> None:
    await _user(db_session)
    tokens = await _login(async_client)

    resp, _ = await _change(
        async_client, tokens["access_token"], new="x" * (get_settings().max_password_length + 1)
    )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_success_rotates_caller_and_kicks_other_devices(async_client, db_session) -> None:
    await _user(db_session)
    phone = await _login(async_client)
    laptop = await _login(async_client)
    guard = require_auth(settings=get_settings())

    resp, send = await _change(async_client, laptop["access_token"])

    assert resp.status_code == 200
    fresh = resp.json()
    assert fresh["token_type"] == "bearer"
    assert fresh["access_token"] != laptop["access_token"]

    # old credential is dead, new one works
    denied = await async_client.post("/auth/login", json={"email": EMAIL, "password": OLD})
    assert denied.status_code == 401
    assert (await _login(async_client, NEW))["access_token"]

    # the other device is out: refresh revoked, access rejected
    assert (
        await async_client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]})
    ).status_code == 401
    with pytest.raises(AuthError, match="revoked"):
        await guard(_request(_bearer(phone["access_token"])))
    # so is the caller's *previous* token pair - it was rotated, not spared
    with pytest.raises(AuthError, match="revoked"):
        await guard(_request(_bearer(laptop["access_token"])))
    assert (
        await async_client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    ).status_code == 401

    # the caller continues with the returned pair
    assert await guard(_request(_bearer(fresh["access_token"])))
    rotated = await async_client.post(
        "/auth/refresh", json={"refresh_token": fresh["refresh_token"]}
    )
    assert rotated.status_code == 200

    # and is told about it
    send.assert_called_once()
    call = send.call_args.kwargs
    assert call["to"] == [EMAIL]
    assert call["template_name"] == "password_changed"
    plain, _ = render_template(call["template_name"], call["context"])
    assert "password" in plain.lower()
