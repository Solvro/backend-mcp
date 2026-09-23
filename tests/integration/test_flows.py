import re
import time
from uuid import uuid4

import httpx
import pytest
from testcontainers.core.waiting_utils import wait_for_logs

pytestmark = pytest.mark.integration


@pytest.fixture
def stack(integration_stack: dict) -> dict:
    return integration_stack


@pytest.fixture
def client(stack: dict):
    with httpx.Client(timeout=20.0) as c:
        yield c


def _verification_token(client: httpx.Client, mailpit_url: str, email: str) -> str:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        messages = client.get(f"{mailpit_url}/api/v1/messages", params={"limit": 50}).json()
        for message in messages.get("messages", []):
            if message.get("Subject") != "Account verification":
                continue
            if not any(to["Address"] == email for to in message.get("To", [])):
                continue
            detail = client.get(f"{mailpit_url}/api/v1/message/{message['ID']}").json()
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


def test_auth_register_login_refresh_logout_and_chat(client: httpx.Client, stack: dict) -> None:
    payload, password = _registration_payload()
    email = payload["email"]
    auth_url = stack["auth_url"]

    registered = client.post(f"{auth_url}/auth/register", json=payload)
    assert registered.status_code == 201

    token = _verification_token(client, stack["mailpit_url"], email)
    verified = client.get(f"{auth_url}/auth/verify", params={"token": token})
    assert verified.status_code == 200

    logged_in = client.post(
        f"{auth_url}/auth/login", json={"email": email, "password": password}
    )
    assert logged_in.status_code == 200
    first_tokens = logged_in.json()

    refreshed = client.post(
        f"{auth_url}/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    second_tokens = refreshed.json()
    assert second_tokens["refresh_token"] != first_tokens["refresh_token"]

    chat = client.post(
        f"{stack['chat_url']}/api/chat",
        json={"message": "Where is room 101?"},
        headers=_bearer(second_tokens["access_token"]),
    )
    assert chat.status_code == 200
    assert chat.json()["message"].startswith("Integration MCP answer")

    logged_out = client.post(
        f"{auth_url}/auth/logout",
        json={"refresh_token": second_tokens["refresh_token"]},
        headers=_bearer(second_tokens["access_token"]),
    )
    assert logged_out.status_code == 204

    replay = client.post(
        f"{auth_url}/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}
    )
    assert replay.status_code == 401


def test_invalid_access_token_is_rejected_by_chat(client: httpx.Client, stack: dict) -> None:
    response = client.post(
        f"{stack['chat_url']}/api/chat",
        json={"message": "Should not be accepted"},
        headers=_bearer("expired-or-invalid-token"),
    )
    assert response.status_code == 401


def test_duplicate_registration_returns_conflict(client: httpx.Client, stack: dict) -> None:
    payload, _ = _registration_payload()

    first = client.post(f"{stack['auth_url']}/auth/register", json=payload)
    duplicate = client.post(f"{stack['auth_url']}/auth/register", json=payload)

    assert first.status_code == 201
    assert duplicate.status_code == 409


def test_invalid_login_password_returns_unauthorized(client: httpx.Client, stack: dict) -> None:
    payload, _ = _registration_payload()
    registered = client.post(f"{stack['auth_url']}/auth/register", json=payload)

    response = client.post(
        f"{stack['auth_url']}/auth/login",
        json={"email": payload["email"], "password": "WrongPassword123!"},
    )

    assert registered.status_code == 201
    assert response.status_code == 401


def test_malformed_chat_payload_returns_unprocessable_entity(
    client: httpx.Client, stack: dict
) -> None:
    response = client.post(f"{stack['chat_url']}/api/chat", json={"wrong_key": "hi"})

    assert response.status_code == 422


def test_chat_returns_service_unavailable_when_mcp_is_down(
    client: httpx.Client, stack: dict
) -> None:
    mcp_stub = stack["mcp_stub_container"]
    try:
        mcp_stub.stop()
        response = client.post(
            f"{stack['chat_url']}/api/chat",
            json={"message": "Does this still work?"},
        )
        assert response.status_code == 503
    finally:
        mcp_stub.start()
        wait_for_logs(mcp_stub, "Application startup complete", timeout=30)
