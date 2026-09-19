import pytest
from auth_app.models import Role, User
from auth_app.security import get_password_manager
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

PASSWORD = "SecretPassword123!"


async def _user(db: AsyncSession, *, email: str, role: str = "user") -> User:
    role_row = (await db.execute(select(Role).where(Role.name == role))).scalar_one()
    user = User(
        username=email.split("@")[0],
        email=email,
        password_hash=get_password_manager().hash_password(PASSWORD),
        email_verified=True,
        roles=[role_row],
    )
    db.add(user)
    await db.commit()
    return user


async def _login(client: AsyncClient, email: str) -> dict:
    resp = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_anonymous_is_401(async_client) -> None:
    assert (await async_client.get("/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_me_returns_the_callers_profile_not_someone_elses(async_client, db_session) -> None:
    alice = await _user(db_session, email="alice@example.com")
    await _user(db_session, email="bob@example.com", role="admin")
    tokens = await _login(async_client, "alice@example.com")

    resp = await async_client.get("/auth/me", headers=_bearer(tokens["access_token"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == alice.id
    assert body["username"] == "alice"
    assert body["email"] == "alice@example.com"
    assert body["email_verified"] is True
    assert body["roles"] == ["user"]
    assert "created_at" in body
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_me_reflects_db_changes_made_after_login(async_client, db_session) -> None:
    user = await _user(db_session, email="alice@example.com")
    tokens = await _login(async_client, "alice@example.com")
    admin = (await db_session.execute(select(Role).where(Role.name == "admin"))).scalar_one()
    user.roles.append(admin)
    await db_session.commit()

    resp = await async_client.get("/auth/me", headers=_bearer(tokens["access_token"]))

    assert sorted(resp.json()["roles"]) == ["admin", "user"]  # fresh from the DB, not the token


@pytest.mark.asyncio
async def test_logged_out_token_is_rejected(async_client, db_session) -> None:
    await _user(db_session, email="alice@example.com")
    tokens = await _login(async_client, "alice@example.com")
    await async_client.post("/auth/logout", json={}, headers=_bearer(tokens["access_token"]))

    assert (
        await async_client.get("/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 401


@pytest.mark.asyncio
async def test_deactivated_account_is_401(async_client, db_session) -> None:
    user = await _user(db_session, email="alice@example.com")
    tokens = await _login(async_client, "alice@example.com")
    user.is_active = False
    await db_session.commit()

    assert (
        await async_client.get("/auth/me", headers=_bearer(tokens["access_token"]))
    ).status_code == 401
