from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from auth_app.models import RefreshToken, Role, User
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

OLD_PASSWORD = "OldPassword123!"
NEW_PASSWORD = "BrandNewPassword456!"
GENERIC = {"message": "If the email is registered, a reset link has been sent."}


async def _user(db: AsyncSession, *, email="alice@example.com", verified=True, active=True) -> User:
    role = (await db.execute(select(Role).where(Role.name == "user"))).scalar_one()
    user = User(
        username=email.split("@")[0],
        email=email,
        password_hash=get_password_manager().hash_password(OLD_PASSWORD),
        email_verified=verified,
        is_active=active,
        roles=[role],
    )
    db.add(user)
    await db.commit()
    return user


def _reset_token(mock_send: AsyncMock) -> str:
    call = mock_send.call_args.kwargs
    assert call["template_name"] == "password_reset"
    url = call["context"]["reset_url"]
    plain, _ = render_template(call["template_name"], call["context"])
    assert url in plain  # context feeds the template
    return parse_qs(urlparse(url).query)["token"][0]


async def _request_reset(client: AsyncClient, email: str) -> tuple[object, AsyncMock]:
    with patch("auth_app.api.auth.send_template_email", new_callable=AsyncMock) as send:
        resp = await client.post("/auth/forgot-password", json={"email": email})
    return resp, send


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


@pytest.mark.asyncio
async def test_unknown_and_known_emails_get_identical_responses(async_client, db_session) -> None:
    await _user(db_session)

    unknown, unknown_send = await _request_reset(async_client, "nobody@example.com")
    known, known_send = await _request_reset(async_client, "alice@example.com")

    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json() == GENERIC
    unknown_send.assert_not_called()
    known_send.assert_called_once()
    assert known_send.call_args.kwargs["to"] == ["alice@example.com"]


@pytest.mark.asyncio
async def test_inactive_account_gets_no_email(async_client, db_session) -> None:
    await _user(db_session, active=False)

    resp, send = await _request_reset(async_client, "alice@example.com")

    assert resp.status_code == 200 and resp.json() == GENERIC
    send.assert_not_called()


@pytest.mark.asyncio
async def test_second_request_within_cooldown_sends_nothing(async_client, db_session) -> None:
    await _user(db_session)

    _, first = await _request_reset(async_client, "alice@example.com")
    again, second = await _request_reset(async_client, "alice@example.com")

    first.assert_called_once()
    second.assert_not_called()
    assert again.status_code == 200 and again.json() == GENERIC


@pytest.mark.asyncio
async def test_reset_token_is_stored_hashed_with_short_ttl(
    async_client, db_session, redis_client
) -> None:
    await _user(db_session)
    _, send = await _request_reset(async_client, "alice@example.com")
    token = _reset_token(send)

    keys = [k async for k in redis_client.scan_iter("pwdreset:*")]
    assert len(keys) == 1
    assert token.encode() not in keys[0]
    ttl = await redis_client.ttl(keys[0])
    assert 0 < ttl <= get_settings().password_reset_token_ttl_minutes * 60


@pytest.mark.asyncio
async def test_valid_token_changes_password_and_verifies_email(async_client, db_session) -> None:
    user = await _user(db_session, verified=False)
    _, send = await _request_reset(async_client, "alice@example.com")
    token = _reset_token(send)

    resp = await async_client.post(
        "/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )

    assert resp.status_code == 200
    assert resp.json() == {"message": "Password has been reset."}
    assert "access_token" not in resp.json()

    await db_session.refresh(user)
    pm = get_password_manager()
    assert pm.verify_password(NEW_PASSWORD, user.password_hash)
    assert not pm.verify_password(OLD_PASSWORD, user.password_hash)
    assert user.email_verified is True and user.verified_at is not None

    login = await async_client.post(
        "/auth/login", json={"email": "alice@example.com", "password": NEW_PASSWORD}
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_reset_token_is_single_use(async_client, db_session) -> None:
    await _user(db_session)
    _, send = await _request_reset(async_client, "alice@example.com")
    token = _reset_token(send)
    body = {"token": token, "new_password": NEW_PASSWORD}

    assert (await async_client.post("/auth/reset-password", json=body)).status_code == 200
    replay = await async_client.post("/auth/reset-password", json=body)

    assert replay.status_code == 400
    assert replay.json()["detail"] == "invalid_or_expired_token"


@pytest.mark.asyncio
async def test_expired_and_forged_tokens_are_indistinguishable(
    async_client, db_session, redis_client
) -> None:
    await _user(db_session)
    _, send = await _request_reset(async_client, "alice@example.com")
    token = _reset_token(send)
    for key in [k async for k in redis_client.scan_iter("pwdreset:*")]:
        await redis_client.delete(key)  # what Redis does when the TTL lapses

    expired = await async_client.post(
        "/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )
    forged = await async_client.post(
        "/auth/reset-password", json={"token": "A" * 43, "new_password": NEW_PASSWORD}
    )

    assert expired.status_code == forged.status_code == 400
    assert expired.json()["detail"] == forged.json()["detail"] == "invalid_or_expired_token"


@pytest.mark.asyncio
async def test_weak_password_is_rejected_and_token_survives(async_client, db_session) -> None:
    await _user(db_session)
    _, send = await _request_reset(async_client, "alice@example.com")
    token = _reset_token(send)

    weak = await async_client.post(
        "/auth/reset-password", json={"token": token, "new_password": "short"}
    )
    assert weak.status_code == 422

    too_long = await async_client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "x" * (get_settings().max_password_length + 1)},
    )
    assert too_long.status_code == 400

    ok = await async_client.post(
        "/auth/reset-password", json={"token": token, "new_password": NEW_PASSWORD}
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_reset_invalidates_every_existing_session(async_client, db_session) -> None:
    await _user(db_session)
    login = await async_client.post(
        "/auth/login", json={"email": "alice@example.com", "password": OLD_PASSWORD}
    )
    tokens = login.json()
    guard = require_auth(settings=get_settings())
    assert await guard(_request({"Authorization": f"Bearer {tokens['access_token']}"}))

    _, send = await _request_reset(async_client, "alice@example.com")
    resp = await async_client.post(
        "/auth/reset-password",
        json={"token": _reset_token(send), "new_password": NEW_PASSWORD},
    )
    assert resp.status_code == 200

    refresh = await async_client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401
    for row in (await db_session.execute(select(RefreshToken))).scalars():
        await db_session.refresh(row)
        assert row.revoked is True

    with pytest.raises(AuthError, match="revoked"):
        await guard(_request({"Authorization": f"Bearer {tokens['access_token']}"}))
