import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parents[2]
COMPOSE_FILE = ROOT / "docker" / "compose.integration.yml"
KEY_DIR = ROOT / "docker" / ".e2e-keys"
AUTH_URL = os.getenv("AUTH_URL", "http://127.0.0.1:18000")
CHAT_URL = os.getenv("CHAT_URL", "http://127.0.0.1:18001")
MAILPIT_URL = os.getenv("MAILPIT_URL", "http://127.0.0.1:18025")


def _run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def _ensure_keys() -> None:
    private_key = KEY_DIR / "jwt_private.pem"
    public_key = KEY_DIR / "jwt_public.pem"
    if private_key.exists() and public_key.exists():
        return
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    _run(
        "openssl",
        "genpkey",
        "-algorithm",
        "RSA",
        "-pkeyopt",
        "rsa_keygen_bits:2048",
        "-out",
        str(private_key),
    )
    _run("openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key))
    for path in (private_key, public_key):
        path.chmod(0o644)


def _wait_for(url: str, *, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code < 500:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(1.0)
    raise RuntimeError(f"{url} did not become ready: {last_error}")


@pytest.fixture(scope="session", autouse=True)
def integration_stack() -> Iterator[None]:
    _ensure_keys()
    try:
        _run("docker", "compose", "-f", str(COMPOSE_FILE),
             "up", "-d", "--build", "--wait")
        stack_up = True
        _wait_for(f"{AUTH_URL}/health")
        _wait_for(f"{CHAT_URL}/health")
        _wait_for(f"{MAILPIT_URL}/readyz")
        yield
    finally:
        if not stack_up:
            subprocess.run(
                ["docker", "compose", "-f", str(COMPOSE_FILE), "logs", "--no-color"],
                cwd=ROOT,
            )
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
            cwd=ROOT, check=False,
        )
