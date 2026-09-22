import re
import subprocess
import time
from uuid import uuid4

import httpx
import pytest

from tests.integration.conftest import AUTH_URL, CHAT_URL, COMPOSE_FILE, MAILPIT_URL, ROOT

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> httpx.Client:
    with httpx.Client(timeout=20.0) as client:
        yield client


def _verification_token(client: httpx.Client, email: str) -> str:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        messages = client.get(f"{MAILPIT_URL}/api/v1/messages", params={"limit": 50}).json()
        for message in messages.get("messages", []):
            if message.get("Subject") != "Account verification":
                continue
            if not any(to["Address"] == email for to in message.get("To", [])):
                continue
            detail = client.get(f"{MAILPIT_URL}/api/v1/message/{message['ID']}").json()
            match = re.search(r"/auth/verify\?token=([^&\s]+)", detail["Text"])
            if match:
                return match.group(1)
        time.sleep(0.5)
    raise AssertionError(f"verification email for {email} was not delivered")


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _registration_payload() -> tuple[dict[str, str], str]:
    suffix = uuid4().hex[:10]
    password = "IntegrationPassword123!"
    return (
        {
            "username": f"integration-{suffix}",
            "email": f"integration-{suffix}@example.com",
            "password": password,
        },
        password,
    )


def test_auth_register_login_refresh_logout_and_chat(client: httpx.Client) -> None:
    payload, password = _registration_payload()
    email = payload["email"]

    registered = client.post(f"{AUTH_URL}/auth/register", json=payload)
    assert registered.status_code == 201

    token = _verification_token(client, email)
    verified = client.get(f"{AUTH_URL}/auth/verify", params={"token": token})
    assert verified.status_code == 200

    logged_in = client.post(f"{AUTH_URL}/auth/login", json={"email": email, "password": password})
    assert logged_in.status_code == 200
    first_tokens = logged_in.json()

    refreshed = client.post(
        f"{AUTH_URL}/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    second_tokens = refreshed.json()
    assert second_tokens["refresh_token"] != first_tokens["refresh_token"]

    chat = client.post(
        f"{CHAT_URL}/api/chat",
        json={"message": "Where is room 101?"},
        headers=_bearer(second_tokens["access_token"]),
    )
    assert chat.status_code == 200
    assert chat.json()["message"].startswith("Integration MCP answer")

    logged_out = client.post(
        f"{AUTH_URL}/auth/logout",
        json={"refresh_token": second_tokens["refresh_token"]},
        headers=_bearer(second_tokens["access_token"]),
    )
    assert logged_out.status_code == 204

    replay = client.post(
        f"{AUTH_URL}/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_invalid_access_token_is_rejected_by_chat(client: httpx.Client) -> None:
    response = client.post(
        f"{CHAT_URL}/api/chat",
        json={"message": "Should not be accepted"},
        headers=_bearer("expired-or-invalid-token"),
    )
    assert response.status_code == 401


def test_duplicate_registration_returns_conflict(client: httpx.Client) -> None:
    payload, _ = _registration_payload()

    first = client.post(f"{AUTH_URL}/auth/register", json=payload)
    duplicate = client.post(f"{AUTH_URL}/auth/register", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_invalid_login_password_returns_unauthorized(client: httpx.Client) -> None:
    payload, _ = _registration_payload()
    registered = client.post(f"{AUTH_URL}/auth/register", json=payload)

    response = client.post(
        f"{AUTH_URL}/auth/login",
        json={"email": payload["email"], "password": "WrongPassword123!"},
    )

    assert registered.status_code == 201
    assert response.status_code == 401


def test_malformed_chat_payload_returns_unprocessable_entity(client: httpx.Client) -> None:
    response = client.post(f"{CHAT_URL}/api/chat", json={"wrong_key": "hi"})

    assert response.status_code == 422


def test_chat_returns_service_unavailable_when_mcp_is_down(client: httpx.Client) -> None:
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "stop", "mcp-stub"],
            cwd=ROOT,
            check=True,
        )
        response = client.post(f"{CHAT_URL}/api/chat", json={"message": "Does this still work?"})
        assert response.status_code == 503
    finally:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "start", "mcp-stub"],
            cwd=ROOT,
            check=False,
        )
