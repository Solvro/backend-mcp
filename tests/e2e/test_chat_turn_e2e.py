from uuid import uuid4

import pytest
from e2e_mail import verification_token

pytestmark = pytest.mark.e2e

PASSWORD = "E2ePassword123!"
QUESTION = "Gdzie jest sala 101?"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_authed_chat_turn_round_trips_through_nginx(edge, mailpit):
    suffix = uuid4().hex[:10]
    email = f"e2e-{suffix}@example.com"

    registered = edge.post(
        "/auth/register",
        json={"username": f"e2e-{suffix}", "email": email, "password": PASSWORD},
    )
    assert registered.status_code == 201, registered.text

    verified = edge.get("/auth/verify", params={"token": verification_token(mailpit, email)})
    assert verified.status_code == 200, verified.text

    logged_in = edge.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    tokens = logged_in.json()

    access = bearer(tokens["access_token"])

    chat = edge.post("/api/chat", json={"message": QUESTION}, headers=access)
    assert chat.status_code == 200, chat.text
    answer = chat.json()
    # The answer really came from the MCP server (docker/mcp-stub), through chat-service.
    assert answer["message"] == f"Integration MCP answer for: {QUESTION}"

    history = edge.get(f"/api/sessions/{answer['session_id']}/history", headers=access)
    assert history.status_code == 200, history.text
    assert [(m["role"], m["content"]) for m in history.json()] == [
        ("user", QUESTION),
        ("assistant", answer["message"]),
    ]

    logged_out = edge.post(
        "/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=access,
    )
    assert logged_out.status_code == 204, logged_out.text

    replay = edge.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
