import os
import time

import pytest
from deploy_harness import (
    DIGEST_1,
    DIGEST_2,
    SHA_A,
    SHA_B,
    compose_action,
    health,
    registry,
    revision,
    stack_conf,
)

pytestmark = pytest.mark.unit


def happy(digest=DIGEST_2, sha=SHA_B, status="ok"):
    return [*registry(digest), revision(digest, sha), health(status)]


def test_new_release_is_migrated_swapped_gated_and_recorded(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = happy()

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    release = str(agent.release(SHA_B))
    docker = [c["args"] for c in agent.calls("docker")]
    git = [c["args"] for c in agent.calls("git")]
    assert ["pull", "-q", f"ghcr.io/solvro/backend-mcp-auth@{DIGEST_2}"] in docker
    assert ["-C", release, "fetch", "-q", "--depth", "1", "origin", SHA_B] in git
    assert ["network", "inspect", "solvro-mcp-internal"] in docker

    compose = agent.compose()
    prefix = [
        "compose", "-p", "backend-mcp",
        "--env-file", str(agent.home / "stacks/backend/.env.prod"),
        "-f", f"{release}/docker/compose.yml",
        "-f", f"{release}/docker/compose.prod.yml",
    ]
    assert all(c["args"][: len(prefix)] == prefix for c in compose)
    assert all(c["release_tag"] == f"sha-{SHA_B}" for c in compose)
    assert [compose_action(c) for c in compose] == [
        ["pull", "--quiet"],
        ["run", "--rm", "migrate"],
        ["up", "-d", "--no-build", "--remove-orphans", "--wait", "--wait-timeout", "180"],
    ]

    assert agent.state("backend.deployed") == f"{SHA_B} {DIGEST_2}"
    assert agent.state("backend.previous") == f"{SHA_A} {DIGEST_1}"
    assert os.readlink(agent.home / "stacks/backend/current") == f"releases/{SHA_B}"


def test_stack_config_can_set_its_own_wait_timeout(agent):
    agent.write_conf("backend", stack_conf(WAIT_TIMEOUT="300"))
    agent.rules = happy()

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    ups = [compose_action(c) for c in agent.compose() if compose_action(c)[0] == "up"]
    assert ups == [
        ["up", "-d", "--no-build", "--remove-orphans", "--wait", "--wait-timeout", "300"]
    ]


def test_degraded_backend_passes_the_gate(agent):
    agent.rules = happy(status="degraded")

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert agent.state("backend.deployed") == f"{SHA_B} {DIGEST_2}"


def test_an_existing_release_checkout_is_reused(agent):
    agent.mark_fetched(SHA_B)
    agent.rules = happy()

    assert agent.tick().returncode == 0
    assert agent.calls("git") == []


@pytest.mark.parametrize("label", ["", "v1.0", "../../etc", "B" * 40])
def test_revision_that_is_not_a_sha_is_refused(agent, label):
    agent.rules = [*registry(DIGEST_2), revision(DIGEST_2, label)]

    result = agent.tick()

    assert result.returncode == 1
    assert "could not read the revision" in result.stderr
    assert agent.compose() == []
    assert agent.calls("git") == []
    assert sorted(os.listdir(agent.home / "stacks/backend/releases")) == []


def test_missing_network_is_retried_not_marked_bad(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = [*happy(), {"cmd": "docker", "match": r"^network inspect", "exit": 1}]

    result = agent.tick()

    assert result.returncode == 1
    assert "does not exist yet" in result.stderr
    assert agent.compose() == []
    assert agent.state("backend.bad") is None
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"


def test_failed_pull_is_retried_not_marked_bad(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = [*happy(), {"cmd": "docker", "match": r" pull --quiet$", "exit": 1}]

    result = agent.tick()

    assert result.returncode == 1
    assert [compose_action(c) for c in agent.compose()] == [["pull", "--quiet"]]
    assert agent.state("backend.bad") is None
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"


def test_stack_without_migrations_env_file_or_health_url(agent):
    agent.write_conf(
        "frontend",
        stack_conf(
            PROJECT="frontend-mcp",
            PRIMARY_IMAGE="solvro/frontend-mcp",
            IMAGES="solvro/frontend-mcp",
            SPARSE_PATHS="deploy",
            COMPOSE_FILES="deploy/compose.yml",
            ENV_FILE="",
            REQUIRED_NETWORKS="backend-mcp_backend",
            MIGRATE_SERVICE="",
            HEALTH_URL="",
        ),
    )
    agent.rules = [
        *registry(DIGEST_2, image="solvro/frontend-mcp"),
        revision(DIGEST_2, SHA_B, image="solvro/frontend-mcp"),
    ]

    result = agent.tick("frontend")

    assert result.returncode == 0, result.stderr
    compose = agent.compose()
    assert [compose_action(c) for c in compose] == [
        ["pull", "--quiet"],
        ["up", "-d", "--no-build", "--remove-orphans", "--wait", "--wait-timeout", "180"],
    ]
    assert all("--env-file" not in c["args"] for c in compose)
    docker_args = [c["args"] for c in agent.calls("docker")]
    assert ["network", "inspect", "backend-mcp_backend"] in docker_args
    assert not any("/health" in " ".join(c["args"]) for c in agent.calls("curl"))


def test_old_releases_are_pruned_but_current_and_previous_survive(agent):
    old = [f"{i:040x}" for i in range(1, 6)]  # five releases newer than SHA_A, oldest first
    now = time.time()
    for age, sha in zip(range(60, 0, -10), [SHA_A, *old]):
        os.utime(agent.mark_fetched(sha), (now - age * 60, now - age * 60))
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")  # the oldest dir
    agent.rules = happy()

    assert agent.tick().returncode == 0

    kept = set(os.listdir(agent.home / "stacks/backend/releases"))
    assert SHA_B in kept  # new current
    assert SHA_A in kept  # previous, although it is the oldest
    assert len(kept) == 6  # the 5 newest + previous
    pruned = {old[0]}
    assert kept.isdisjoint(pruned)
    removed = [c["args"] for c in agent.calls("docker") if c["args"][:2] == ["image", "rm"]]
    assert ["image", "rm", f"ghcr.io/solvro/backend-mcp-chat:sha-{old[0]}"] in removed
