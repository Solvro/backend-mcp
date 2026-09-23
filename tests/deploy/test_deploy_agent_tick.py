import pytest
from deploy_harness import DIGEST_1, DIGEST_2, SHA_A, registry

pytestmark = pytest.mark.unit


def test_paused_tick_touches_nothing(agent):
    agent.set_state("paused", "")

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert agent.calls("curl") == []
    assert agent.calls("docker") == []


def test_tick_skips_when_another_deploy_holds_the_lock(agent):
    agent.rules = [{"cmd": "flock", "exit": 1}]

    result = agent.tick()

    assert result.returncode == 0
    assert "holds the lock" in result.stderr
    assert agent.calls("curl") == []


def test_unchanged_digest_is_a_no_op(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = registry(DIGEST_1)

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert agent.calls("docker") == []


def test_digest_marked_bad_is_not_retried(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.set_state("backend.bad", f"{DIGEST_2}\n")
    agent.rules = registry(DIGEST_2)

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert agent.calls("docker") == []


def test_registry_failure_changes_nothing(agent):
    agent.set_state("backend.deployed", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = [{"cmd": "curl", "match": "/token", "exit": 22}]

    result = agent.tick()

    assert result.returncode == 1
    assert "could not resolve" in result.stderr
    assert agent.calls("docker") == []
    assert agent.state("backend.bad") is None
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"


def test_unknown_stack_is_a_usage_error(agent):
    result = agent.mcpwr("nope", "tick")

    assert result.returncode == 2
    assert "unknown stack" in result.stderr
