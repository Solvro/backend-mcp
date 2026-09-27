"""The MCP gateway's failure paths (MCP-1/MCP-2), driven by httpbin standing in for the MCP server.

Each test talks straight to its own chat-service instance (see docker/compose.e2e.yml): these
instances exist only to point MCP_SERVER_URL at a misbehaving upstream, so they are not behind
nginx. Their retry and breaker settings are small and fixed in the compose file.
"""

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
    # httpbin would answer after 5 s; the gateway gave up on its own budget first.
    assert elapsed < HTTPBIN_DELAY - 0.5, elapsed


def test_repeated_mcp_outages_open_the_circuit_breaker(chat_broken):
    first = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert first.status_code == 503, first.text
    # Closed circuit: the next request goes upstream again, so only the retry ceiling.
    assert first.headers["Retry-After"] == str(RETRY_CEILING)

    second = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert second.status_code == 503, second.text
    # The second failure reaches the threshold (2): the breaker opens for the full window.
    assert second.headers["Retry-After"] == str(RESET_WINDOW)

    time.sleep(2)
    third = chat_broken.post("/api/chat", json={"message": QUESTION})
    assert third.status_code == 503, third.text
    # Open circuit: failed fast without calling upstream, advertising what is left of the window.
    assert RESET_WINDOW - 10 <= int(third.headers["Retry-After"]) <= RESET_WINDOW - 2


def test_health_is_degraded_while_the_mcp_server_is_down(chat_broken):
    response = chat_broken.get("/health")

    # MCP is an optional dependency: the service stays up (200) and says it is degraded. This
    # comes from the readiness probe's own ping of the MCP server, not from the breaker.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded", body
    assert body["checks"]["mcp"]["status"] == "down"
