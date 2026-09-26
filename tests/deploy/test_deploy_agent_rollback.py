import os

import pytest
from deploy_harness import (
    DIGEST_1,
    DIGEST_2,
    DIGEST_3,
    SHA_A,
    SHA_B,
    SHA_C,
    compose_action,
    health,
    registry,
    revision,
)

pytestmark = pytest.mark.unit

SERVICES = {
    "cmd": "docker",
    "match": r" config --services$",
    "stdout": "postgres\nredis\nmigrate\nauth-service\nchat-service\nnginx\n",
}


def running_release(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.mark_fetched(SHA_A)


def new_release():
    return [*registry(DIGEST_2), revision(DIGEST_2, SHA_B)]


def test_failed_migration_marks_the_release_bad_and_keeps_the_old_one(agent):
    running_release(agent)
    agent.rules = [*new_release(), {"cmd": "docker", "match": r" run --rm migrate$", "exit": 1}]

    result = agent.tick()

    assert result.returncode == 1
    assert [compose_action(c) for c in agent.compose()] == [
        ["pull", "--quiet"],
        ["run", "--rm", "migrate"],
    ]
    assert agent.state("backend.bad") == DIGEST_2
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"


def test_failed_health_gate_rolls_back_without_rerunning_migrations(agent):
    running_release(agent)
    agent.rules = [
        *new_release(),
        health("unhealthy", exit=22, times=2),  # both attempts of the new release's gate
        health("ok"),  # the rolled-back release's gate
        SERVICES,
    ]

    result = agent.tick()

    assert result.returncode == 1
    assert f"rolling backend back to {SHA_A}" in result.stderr
    rollback = [compose_action(c) for c in agent.compose() if c["release_tag"] == f"sha-{SHA_A}"]
    assert rollback == [
        # A rollback must work while the registry is down: its images are never pruned.
        ["pull", "--quiet", "--ignore-pull-failures"],
        ["config", "--services"],
        [
            "up", "-d", "--no-build", "--no-deps", "--remove-orphans",
            "--wait", "--wait-timeout", "180",
            "postgres", "redis", "auth-service", "chat-service", "nginx",
        ],
    ]
    assert agent.state("backend.bad") == DIGEST_2
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"
    assert os.readlink(agent.home / "stacks/backend/current") == f"releases/{SHA_A}"


def test_failed_swap_rolls_back(agent):
    running_release(agent)
    agent.rules = [
        *new_release(),
        {"cmd": "docker", "match": r" up -d --no-build --remove-orphans ", "exit": 1},
        health("ok"),
        SERVICES,
    ]

    result = agent.tick()

    assert result.returncode == 1
    ups = [compose_action(c)[:4] for c in agent.compose() if compose_action(c)[0] == "up"]
    assert ups == [
        ["up", "-d", "--no-build", "--remove-orphans"],
        ["up", "-d", "--no-build", "--no-deps"],
    ]
    assert agent.state("backend.bad") == DIGEST_2


def test_first_deploy_failure_has_nothing_to_roll_back_to(agent):
    agent.rules = [*new_release(), {"cmd": "docker", "match": r" up -d ", "exit": 1}]

    result = agent.tick()

    assert result.returncode == 1
    assert "nothing to roll back to" in result.stderr
    assert [compose_action(c)[0] for c in agent.compose()].count("up") == 1
    assert agent.state("backend.bad") == DIGEST_2
    assert agent.state("backend.deployed") is None


def test_failed_rollback_is_reported_loudly(agent):
    running_release(agent)
    agent.rules = [*new_release(), {"cmd": "docker", "match": r" up -d ", "exit": 1}, SERVICES]

    result = agent.tick()

    assert result.returncode == 1
    assert f"ROLLBACK OF backend TO {SHA_A} FAILED" in result.stderr


def test_new_main_after_a_bad_release_deploys_and_clears_the_mark(agent):
    running_release(agent)
    agent.set_state("backend.bad", f"{DIGEST_2}\n")
    agent.rules = [*registry(DIGEST_3), revision(DIGEST_3, SHA_C), health("ok")]

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert agent.state("backend.bad") is None
    assert agent.state("backend.deployed") == f"{SHA_C} {DIGEST_3}"
