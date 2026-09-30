import time

import pytest

pytestmark = pytest.mark.e2e

QUESTION = "Gdzie jest sala 101?"
RETRY_CEILING = 2  # MCP_RETRY_MAX_DELAY: advertised while the circuit is closed
RESET_WINDOW = 60  # MCP_BREAKER_RESET_TIMEOUT_SECONDS of chat-broken
HTTPBIN_DELAY = 5  # chat-slow's upstream is httpbin /delay/5; its MCP timeouts are 2 s


def test_an_mcp_call_that_outlives_its_timeout_answers_503_without_waiting(chat_slow):
    started = time.monotonic()
    response = chat_slow.post("/api/chat", json={"message": QUESTION})
    elapsed = time.monotonic() - started

    assert response.status_code == 503, response.text
    assert response.headers["Retry-After"] == str(RETRY_CEILING)
    assert elapsed < HTTPBIN_DELAY - 0.5, elapsed


def test_repeated_mcp_outages_open_the_circuit_breaker(chat_broken):
    first = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert first.status_code == 503, first.text
    assert first.headers["Retry-After"] == str(RETRY_CEILING)

    second = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert second.status_code == 503, second.text
    assert second.headers["Retry-After"] == str(RESET_WINDOW)

    time.sleep(2)
    third = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert third.status_code == 503, third.text
    assert RESET_WINDOW - 10 <= int(third.headers["Retry-After"]) <= RESET_WINDOW - 2


def test_health_is_degraded_while_the_mcp_server_is_down(chat_broken):
    response = chat_broken.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded", body
    assert body["checks"]["mcp"]["status"] == "down"
