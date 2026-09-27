"""Fixtures for the e2e suite, which drives the stack from docker/compose.e2e.yml.

`just test-e2e` brings the stack up (and down again); the URLs below match its published ports
and can be overridden with E2E_* variables when the stack runs elsewhere.
"""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

EDGE_URL = os.getenv("E2E_EDGE_URL", "https://localhost:18443")
CHAT_SLOW_URL = os.getenv("E2E_CHAT_SLOW_URL", "http://localhost:18011")
CHAT_BROKEN_URL = os.getenv("E2E_CHAT_BROKEN_URL", "http://localhost:18012")
MAILPIT_URL = os.getenv("E2E_MAILPIT_URL", "http://localhost:8025")
COMPOSE = ["docker", "compose", "-f", str(Path(__file__).parents[2] / "docker/compose.e2e.yml")]


def _client(base_url: str, **kwargs) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=30.0, **kwargs) as client:
        yield client


@pytest.fixture
def edge() -> Iterator[httpx.Client]:
    """The nginx TLS edge, with the throwaway self-signed cert from `just tls-selfsigned`."""
    yield from _client(EDGE_URL, verify=False)


@pytest.fixture
def chat_slow() -> Iterator[httpx.Client]:
    """A chat-service whose MCP server is httpbin's /delay: every MCP call outlives its timeout."""
    yield from _client(CHAT_SLOW_URL)


@pytest.fixture(scope="module")
def chat_broken() -> Iterator[httpx.Client]:
    """A chat-service whose MCP server is httpbin's /status/503: every MCP call is an outage.

    Recreated first: its breaker only closes again after a successful MCP call, which this
    upstream never gives, so a second run against a kept stack (E2E_KEEP=1) would start with
    the circuit already open.
    """
    subprocess.run(
        [*COMPOSE, "up", "-d", "--no-deps", "--force-recreate", "--wait", "chat-broken"],
        check=True,
        capture_output=True,
    )
    yield from _client(CHAT_BROKEN_URL)


@pytest.fixture
def mailpit() -> Iterator[httpx.Client]:
    yield from _client(MAILPIT_URL)
