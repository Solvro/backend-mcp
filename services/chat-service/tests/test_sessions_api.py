import jwt
import pytest
from chat_app.api.sessions import build_sessions_router, get_repository
from chat_app.sessionizer import ConversationRepository, MessageRole
from chat_app.settings import ChatSettings
from common.exceptions_handlers import register_exception_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mongomock_motor import AsyncMongoMockClient

pytestmark = pytest.mark.unit

_SECRET = "test-secret-key-at-least-32-bytes-long"


@pytest.fixture
def repo() -> ConversationRepository:
    db = AsyncMongoMockClient()["chat_test"]
    return ConversationRepository(db)


@pytest.fixture
def client(repo: ConversationRepository) -> TestClient:
    settings = ChatSettings(jwt_secret_key=_SECRET)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(build_sessions_router(settings))
    app.dependency_overrides[get_repository] = lambda: repo
    return TestClient(app)


def _auth(user_id: str) -> dict[str, str]:
    token = jwt.encode({"sub": user_id}, _SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_anonymous_requests_are_rejected(client: TestClient) -> None:
    assert client.get("/api/sessions/x").status_code == 401
    assert client.get("/api/sessions/x/history").status_code == 401
    assert client.get("/api/users/me/sessions").status_code == 401
    assert client.delete("/api/sessions/x").status_code == 401
    assert client.post("/api/sessions/x/deactivate").status_code == 401


def test_invalid_token_is_rejected(client: TestClient) -> None:
    resp = client.get("/api/sessions/x", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_get_own_session_returns_it(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("user-1")

    resp = client.get(f"/api/sessions/{conv.session_id}", headers=_auth("user-1"))

    assert resp.status_code == 200
    assert resp.json()["session_id"] == conv.session_id


async def test_get_other_users_session_is_404(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("owner")

    resp = client.get(f"/api/sessions/{conv.session_id}", headers=_auth("intruder"))

    assert resp.status_code == 404


def test_get_missing_session_is_404(client: TestClient) -> None:
    resp = client.get("/api/sessions/does-not-exist", headers=_auth("user-1"))
    assert resp.status_code == 404


async def test_history_returns_messages_in_order(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("user-1")
    await repo.append_message(conv.session_id, MessageRole.USER, "one")
    await repo.append_message(conv.session_id, MessageRole.ASSISTANT, "two")

    resp = client.get(f"/api/sessions/{conv.session_id}/history", headers=_auth("user-1"))

    assert resp.status_code == 200
    assert [m["content"] for m in resp.json()] == ["one", "two"]


async def test_history_limit_keeps_most_recent(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("user-1")
    for i in range(3):
        await repo.append_message(conv.session_id, MessageRole.USER, f"m{i}")

    resp = client.get(
        f"/api/sessions/{conv.session_id}/history",
        params={"limit": 1},
        headers=_auth("user-1"),
    )

    assert [m["content"] for m in resp.json()] == ["m2"]


async def test_history_of_other_user_is_404(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("owner")
    await repo.append_message(conv.session_id, MessageRole.USER, "secret")

    resp = client.get(f"/api/sessions/{conv.session_id}/history", headers=_auth("intruder"))

    assert resp.status_code == 404


def test_history_limit_out_of_range_is_422(client: TestClient) -> None:
    resp = client.get(
        "/api/sessions/x/history", params={"limit": 0}, headers=_auth("user-1")
    )
    assert resp.status_code == 422


async def test_list_only_returns_callers_sessions(client: TestClient, repo) -> None:
    mine = await repo.create_conversation("user-1")
    await repo.create_conversation("someone-else")

    resp = client.get("/api/users/me/sessions", headers=_auth("user-1"))

    ids = [c["session_id"] for c in resp.json()]
    assert ids == [mine.session_id]


async def test_list_active_only_and_pagination(client: TestClient, repo) -> None:
    keep = await repo.create_conversation("user-1")
    gone = await repo.create_conversation("user-1")
    await repo.deactivate(gone.session_id)

    active = client.get(
        "/api/users/me/sessions", params={"active_only": True}, headers=_auth("user-1")
    )
    assert [c["session_id"] for c in active.json()] == [keep.session_id]

    page = client.get(
        "/api/users/me/sessions", params={"limit": 1, "skip": 1}, headers=_auth("user-1")
    )
    assert len(page.json()) == 1


async def test_delete_removes_session(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("user-1")

    deleted = client.delete(f"/api/sessions/{conv.session_id}", headers=_auth("user-1"))
    assert deleted.status_code == 204

    follow_up = client.get(f"/api/sessions/{conv.session_id}", headers=_auth("user-1"))
    assert follow_up.status_code == 404


async def test_delete_other_users_session_is_404_and_keeps_it(
    client: TestClient, repo
) -> None:
    conv = await repo.create_conversation("owner")

    resp = client.delete(f"/api/sessions/{conv.session_id}", headers=_auth("intruder"))

    assert resp.status_code == 404
    assert await repo.get_conversation(conv.session_id) is not None


async def test_deactivate_reflected_in_reads(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("user-1")

    resp = client.post(
        f"/api/sessions/{conv.session_id}/deactivate", headers=_auth("user-1")
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    read = client.get(f"/api/sessions/{conv.session_id}", headers=_auth("user-1"))
    assert read.json()["is_active"] is False


async def test_deactivate_other_users_session_is_404(client: TestClient, repo) -> None:
    conv = await repo.create_conversation("owner")

    resp = client.post(
        f"/api/sessions/{conv.session_id}/deactivate", headers=_auth("intruder")
    )

    assert resp.status_code == 404
    assert (await repo.get_conversation(conv.session_id)).is_active is True
