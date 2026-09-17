import pytest
from auth_app.models import ADMIN_ROLE, USER_ROLE, Role, User
from auth_app.security import get_password_manager
from auth_app.settings import get_settings
from common.auth import require_roles
from common.exceptions_handlers import register_exception_handlers
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

pytestmark = pytest.mark.unit

PASSWORD = "SecretPassword123!"


async def _login_as(async_client, db, *, email: str, role: str) -> str:
    role_row = (await db.execute(select(Role).where(Role.name == role))).scalar_one()
    db.add(
        User(
            username=email.split("@")[0],
            email=email,
            password_hash=get_password_manager().hash_password(PASSWORD),
            email_verified=True,
            roles=[role_row],
        )
    )
    await db.commit()
    resp = await async_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _admin_only_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get(
        "/admin/ping", dependencies=[Depends(require_roles(ADMIN_ROLE, settings=get_settings()))]
    )
    async def ping() -> dict:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_user_role_is_forbidden_on_admin_route(async_client, db_session) -> None:
    token = await _login_as(async_client, db_session, email="alice@example.com", role=USER_ROLE)

    async with AsyncClient(
        transport=ASGITransport(app=_admin_only_app()), base_url="http://t"
    ) as c:
        resp = await c.get("/admin/ping", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_role_passes_admin_route(async_client, db_session) -> None:
    token = await _login_as(async_client, db_session, email="root@example.com", role=ADMIN_ROLE)

    async with AsyncClient(
        transport=ASGITransport(app=_admin_only_app()), base_url="http://t"
    ) as c:
        resp = await c.get("/admin/ping", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_anonymous_is_unauthorized_not_forbidden() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_admin_only_app()), base_url="http://t"
    ) as c:
        resp = await c.get("/admin/ping")

    assert resp.status_code == 401
