"""The production edge (gateway/nginx/templates.proxy) in Docker, in front of stub upstreams
that echo what they received: path, X-Forwarded-For and X-Forwarded-Proto."""

import subprocess
import time
import uuid
from types import SimpleNamespace

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.integration

NGINX = "nginx:1.27-alpine"
CURL = "curlimages/curl:8.10.1"
STUB_CONF = """server {
    listen 3000;
    listen 8000;
    location / {
    default_type text/plain;
    return 200 "${STUB_NAME} $request_uri xff=$http_x_forwarded_for proto=$http_x_forwarded_proto";
    }
}
"""


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, timeout=120
    ).stdout.strip()


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    if not docker_available():
        pytest.skip("needs a running Docker daemon")
    tag = uuid.uuid4().hex[:8]
    net = f"mcpwr-edge-{tag}"
    stub_dir = tmp_path_factory.mktemp("stub")
    (stub_dir / "default.conf.template").write_text(STUB_CONF)
    started: list[str] = []

    def run(name: str, *args: str) -> str:
        container = f"{name}-{tag}"
        docker("run", "-d", "--name", container, "--network", net, *args)
        started.append(container)
        return container

    docker("network", "create", net)
    try:
        subnet = docker("network", "inspect", net, "--format", "{{(index .IPAM.Config 0).Subnet}}")
        for alias in ("frontend", "chat-service", "auth-service"):
            run(
                f"stub-{alias}", "--network-alias", alias,
                "-e", f"STUB_NAME={alias}", "-e", "NGINX_ENVSUBST_FILTER=^STUB_NAME$",
                "-v", f"{stub_dir}:/etc/nginx/templates:ro", NGINX,
            )
        edges = {}
        for label, cidr in (("untrusted", "127.0.0.1/32"), ("trusted", subnet)):
            edges[label] = run(
                f"edge-{label}", "--network-alias", f"edge-{label}",
                "-e", f"TRUSTED_PROXY_CIDR={cidr}",
                "-e", "NGINX_ENVSUBST_FILTER=^TRUSTED_PROXY_CIDR$",
                "-v", f"{ROOT / 'gateway/nginx/nginx.conf'}:/etc/nginx/nginx.conf:ro",
                "-v", f"{ROOT / 'gateway/nginx/templates.proxy'}:/etc/nginx/templates:ro",
                NGINX,
            )
        client = run("client", "--entrypoint", "sleep", CURL, "600")
        client_ip = docker(
            "inspect", client, "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        )

        def get(edge_label: str, path: str, *headers: str) -> str:
            args = ["exec", client, "curl", "-s", "--max-time", "5"]
            for header in headers:
                args += ["-H", header]
            return docker(*args, f"http://edge-{edge_label}{path}", check=False)

        for _ in range(40):
            if all(get(label, "/health/live").startswith("chat-service") for label in edges):
                break
            time.sleep(0.5)
        else:
            logs = {label: docker("logs", c, check=False) for label, c in edges.items()}
            pytest.fail(f"edge never became ready: {logs}")

        yield SimpleNamespace(get=get, client_ip=client_ip, edges=edges)
    finally:
        for container in started:
            docker("rm", "-f", container, check=False)
        docker("network", "rm", net, check=False)


@pytest.mark.parametrize(
    "path",
    ["/", "/bff/auth/session", "/auth/verify?token=x", "/api/chat", "/auth/login",
     "/.well-known/jwks.json", "/metrics", "/docs"],
)
def test_everything_but_health_goes_to_the_frontend(edge, path):
    assert edge.get("untrusted", path).startswith(f"frontend {path} ")


@pytest.mark.parametrize("path", ["/health", "/health/live"])
def test_health_goes_to_chat_service(edge, path):
    assert edge.get("untrusted", path).startswith(f"chat-service {path} ")


def test_untrusted_peer_cannot_choose_the_client_ip(edge):
    body = edge.get("untrusted", "/x", "X-Forwarded-For: 203.0.113.7")

    assert f"xff={edge.client_ip} " in body
    assert "203.0.113.7" not in body


def test_trusted_proxy_names_the_client(edge):
    body = edge.get("trusted", "/x", "X-Forwarded-For: 203.0.113.7")

    assert "xff=203.0.113.7 " in body


def test_client_supplied_chain_is_collapsed_to_one_address(edge):
    body = edge.get("trusted", "/x", "X-Forwarded-For: 198.51.100.1, 203.0.113.7")

    assert "xff=203.0.113.7 " in body
    assert "198.51.100.1" not in body


def test_upstreams_are_told_the_request_was_https(edge):
    assert edge.get("untrusted", "/").endswith("proto=https")


def test_access_log_records_the_peer_and_what_it_forwarded(edge):
    edge.get("trusted", "/log-probe", "X-Forwarded-For: 203.0.113.9")

    assert f'peer={edge.client_ip} xff="203.0.113.9"' in docker("logs", edge.edges["trusted"])
