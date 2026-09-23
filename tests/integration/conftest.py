import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from testcontainers.community.mongodb import MongoDbContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs

ROOT = Path(__file__).parents[2]
KEY_DIR = ROOT / "docker" / ".e2e-keys"

BUILD_TARGETS = {
    "ml-mcp-backend-integration-mcp-stub": ("docker/mcp-stub", "docker/mcp-stub/Dockerfile"),
    "ml-mcp-backend-integration-auth-service": (".", "services/auth-service/Dockerfile"),
    "ml-mcp-backend-integration-chat-service": (".", "services/chat-service/Dockerfile"),
    "ml-mcp-backend-integration-migrate": (".", "services/auth-service/Dockerfile"),
}


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


def _build_images() -> None:
    for tag, (context, dockerfile) in BUILD_TARGETS.items():
        _run("docker", "build", "-t", tag, "-f", dockerfile, context)


def _wait_http(url: str, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=3.0)
            if response.status_code < 500:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not become ready: {last_error}")


def _start_service(
    image: str,
    *,
    net: Network,
    alias: str,
    port: int,
    env: dict[str, str],
    mount_keys: bool = False,
) -> DockerContainer:
    container = DockerContainer(image)
    container.with_network(net).with_network_aliases(alias).with_exposed_ports(port)
    for key, value in env.items():
        container.with_env(key, value)
    if mount_keys:
        container.with_volume_mapping(KEY_DIR.as_posix(), "/run/integration-keys", "ro")
    container.start()
    wait_for_logs(container, "Application startup complete", timeout=60)
    return container


@pytest.fixture(scope="session", autouse=True)
def _integration_images() -> None:
    _ensure_keys()
    _build_images()


@pytest.fixture(scope="session")
def integration_stack() -> Iterator[dict]:
    _ensure_keys()
    net = Network()
    net.create()

    pg = PostgresContainer(
        "postgres:16-alpine",
        username="postgres",
        password="postgres",
        dbname="mcp_backend",
    )
    pg.with_network(net).with_network_aliases("postgres").start()

    mongo = MongoDbContainer("mongo:7")
    mongo.with_network(net).with_network_aliases("mongo").start()

    redis = RedisContainer("redis:7-alpine")
    redis.with_network(net).with_network_aliases("redis").start()

    mailpit = DockerContainer("axllent/mailpit:latest")
    (
        mailpit.with_network(net)
        .with_network_aliases("mailpit")
        .with_env("MP_SMTP_AUTH_ACCEPT_ANY", "true")
        .with_env("MP_SMTP_AUTH_ALLOW_INSECURE", "true")
        .with_exposed_ports(8025)
        .start()
    )
    mailpit_port = mailpit.get_exposed_port(8025)
    _wait_http(f"http://127.0.0.1:{mailpit_port}/readyz")

    mcp_stub = _start_service(
        "ml-mcp-backend-integration-mcp-stub",
        net=net,
        alias="mcp-stub",
        port=8005,
        env={},
    )

    migrate = DockerContainer("ml-mcp-backend-integration-migrate")
    (
        migrate.with_network(net)
        .with_command("alembic upgrade head")
        .with_env(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@postgres:5432/mcp_backend",
        )
        .start()
    )
    exit_code = migrate.get_wrapped_container().wait()["StatusCode"]
    assert exit_code == 0, "alembic upgrade head failed"

    auth = _start_service(
        "ml-mcp-backend-integration-auth-service",
        net=net,
        alias="auth-service",
        port=8000,
        env={
            "DATABASE_URL": "postgresql+asyncpg://postgres:postgres@postgres:5432/mcp_backend",
            "REDIS_URL": "redis://redis:6379",
            "JWT_ALGORITHM": "RS256",
            "JWT_PRIVATE_KEY_FILE": "/run/integration-keys/jwt_private.pem",
            "JWT_PUBLIC_KEY_FILE": "/run/integration-keys/jwt_public.pem",
            "SMTP_HOST": "mailpit",
            "SMTP_PORT": "1025",
            "SMTP_STARTTLS": "false",
            "RATE_LIMIT_ENABLED": "false",
        },
        mount_keys=True,
    )

    chat = _start_service(
        "ml-mcp-backend-integration-chat-service",
        net=net,
        alias="chat-service",
        port=8000,
        env={
            "MONGO_URI": "mongodb://test:test@mongo:27017",
            "REDIS_URL": "redis://redis:6379",
            "MCP_SERVER_URL": "http://mcp-stub:8005/mcp",
            "JWT_ALGORITHM": "RS256",
            "JWT_PUBLIC_KEY_FILE": "/run/integration-keys/jwt_public.pem",
            "RATE_LIMIT_ENABLED": "false",
            "ANSWER_CACHE_ENABLED": "false",
        },
        mount_keys=True,
    )

    yield {
        "auth_url": f"http://127.0.0.1:{auth.get_exposed_port(8000)}",
        "chat_url": f"http://127.0.0.1:{chat.get_exposed_port(8000)}",
        "mailpit_url": f"http://127.0.0.1:{mailpit_port}",
        "mcp_stub_container": mcp_stub,
        "chat_container": chat,
    }
