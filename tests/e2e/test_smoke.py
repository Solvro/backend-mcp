import os
import time
import urllib.error
import urllib.request

import pytest

AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8000")
CHAT_URL = os.getenv("CHAT_URL", "http://localhost:8001")
HTTPBIN_URL = os.getenv("HTTPBIN_URL", "http://localhost:8080")

pytestmark = pytest.mark.e2e


def _get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


def _get_with_retry(url: str, attempts: int = 10, delay: float = 1.0) -> tuple[int, bytes]:
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            return _get(url)
        except (urllib.error.URLError, ConnectionError, OSError) as err:  # not ready yet
            last_err = err
            time.sleep(delay)
    raise AssertionError(f"{url} never became reachable: {last_err}")


def test_auth_service_health() -> None:
    status, body = _get_with_retry(f"{AUTH_URL}/health")
    assert status == 200
    assert b"auth-service" in body


def test_chat_service_health() -> None:
    status, body = _get_with_retry(f"{CHAT_URL}/health")
    assert status == 200
    assert b"chat-service" in body


def test_httpbin_status_double() -> None:
    status, _ = _get_with_retry(f"{HTTPBIN_URL}/status/200")
    assert status == 200


def test_httpbin_delay_supports_timeout_scenarios() -> None:
    start = time.monotonic()
    status, _ = _get(f"{HTTPBIN_URL}/delay/1", timeout=5.0)
    assert status == 200
    assert time.monotonic() - start >= 1.0
