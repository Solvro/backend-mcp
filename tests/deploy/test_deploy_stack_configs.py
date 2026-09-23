import subprocess

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

CONF_DIR = ROOT / "deploy/agent/stacks"
UNITS = ROOT / "deploy/systemd"
FIELDS = (
    "REPO_URL", "SPARSE_PATHS", "PROJECT", "PRIMARY_IMAGE", "IMAGES",
    "COMPOSE_FILES", "ENV_FILE", "REQUIRED_NETWORKS", "MIGRATE_SERVICE", "HEALTH_URL",
)
EXPECTED = {
    "backend": {
        "REPO_URL": "https://github.com/Solvro/backend-mcp.git",
        "PROJECT": "backend-mcp",
        "PRIMARY_IMAGE": "solvro/backend-mcp-auth",
        "IMAGES": "solvro/backend-mcp-auth solvro/backend-mcp-chat",
        "ENV_FILE": "/opt/mcpwr/stacks/backend/.env.prod",
        "REQUIRED_NETWORKS": "solvro-mcp-internal",
        "MIGRATE_SERVICE": "migrate",
        "HEALTH_URL": "/health",
    },
    "ml-mcp": {
        "REPO_URL": "https://github.com/Solvro/ml-mcp.git",
        "PROJECT": "ml-mcp",
        "PRIMARY_IMAGE": "solvro/ml-mcp-server",
        "COMPOSE_FILES": "docker/compose.stack.yml docker/compose.prod.yml",
        "ENV_FILE": "/opt/mcpwr/stacks/ml-mcp/.env",
        "REQUIRED_NETWORKS": "solvro-mcp-internal",
        "MIGRATE_SERVICE": "",
        "HEALTH_URL": "",
    },
    "frontend": {
        "REPO_URL": "https://github.com/Solvro/frontend-mcp.git",
        "PROJECT": "frontend-mcp",
        "PRIMARY_IMAGE": "solvro/frontend-mcp",
        "COMPOSE_FILES": "deploy/compose.yml",
        "ENV_FILE": "",
        "REQUIRED_NETWORKS": "backend-mcp_backend",
        "MIGRATE_SERVICE": "",
        "HEALTH_URL": "",
    },
}


def load(stack: str) -> dict[str, str]:
    values = " ".join(f'"${name}"' for name in FIELDS)
    script = f'set -u; MCPWR_HOME=/opt/mcpwr; . "{CONF_DIR / stack}.conf"; printf "%s\\n" {values}'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    return dict(zip(FIELDS, out.split("\n")))


@pytest.mark.parametrize("stack", sorted(EXPECTED))
def test_stack_config_matches_the_spec(stack):
    conf = load(stack)

    for key, value in EXPECTED[stack].items():
        assert conf[key] == value, key
    assert conf["PRIMARY_IMAGE"] in conf["IMAGES"].split()


def test_backend_config_points_at_files_in_this_repo():
    conf = load("backend")

    for path in conf["COMPOSE_FILES"].split():
        assert (ROOT / path).is_file(), path
    for path in conf["SPARSE_PATHS"].split():
        assert (ROOT / path).is_dir(), path


def test_units_run_the_installed_agent_every_minute():
    service = (UNITS / "mcpwr-deploy@.service").read_text()
    timer = (UNITS / "mcpwr-deploy@.timer").read_text()

    assert "ExecStart=/opt/mcpwr/agent/mcpwr-deploy %i tick" in service
    assert "User=mcpwr-deploy" in service
    assert "OnUnitActiveSec=60" in timer
    assert "WantedBy=timers.target" in timer
