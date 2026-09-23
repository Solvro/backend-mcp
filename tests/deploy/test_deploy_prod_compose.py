import importlib.util
import shutil

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def checker():
    if shutil.which("docker") is None:
        pytest.skip("needs the docker CLI for `docker compose config`")
    spec = importlib.util.spec_from_file_location(
        "check_prod_secrets", ROOT / "scripts" / "check_prod_secrets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def config(checker) -> dict:
    return checker.rendered_config()


@pytest.fixture(scope="module")
def services(config) -> dict:
    return config["services"]


def test_services_run_the_published_images(services):
    assert services["migrate"]["image"] == "ghcr.io/solvro/backend-mcp-auth:sha-check"
    assert services["auth-service"]["image"] == "ghcr.io/solvro/backend-mcp-auth:sha-check"
    assert services["chat-service"]["image"] == "ghcr.io/solvro/backend-mcp-chat:sha-check"


def test_services_trust_nginx_and_the_frontend_for_forwarded_for(services):
    for name in ("auth-service", "chat-service"):
        assert services[name]["environment"]["FORWARDED_ALLOW_IPS"] == "10.89.0.10,10.89.0.11"


def test_services_mount_no_dev_keys(services):
    for name in ("auth-service", "chat-service"):
        assert not services[name].get("volumes"), name


def test_dynamic_addresses_never_collide_with_the_fixed_ones(config):
    # nginx is 10.89.0.10 and the frontend 10.89.0.11; Docker hands out the rest from .128 up.
    assert config["networks"]["backend"]["ipam"]["config"] == [
        {"subnet": "10.89.0.0/24", "ip_range": "10.89.0.128/25"}
    ]


def test_whole_stack_policy_check_passes(checker):
    assert checker.main() == 0
