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


def test_status_reports_every_field(agent):
    agent.set_state("backend.deployed", f"{SHA_B} {DIGEST_2}\n")
    agent.set_state("backend.previous", f"{SHA_A} {DIGEST_1}\n")
    agent.set_state("paused", "")

    result = agent.mcpwr("backend", "status")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "stack:    backend",
        f"deployed: {SHA_B} {DIGEST_2}",
        f"previous: {SHA_A} {DIGEST_1}",
        "bad:      -",
        "paused:   yes",
    ]


def test_manual_deploy_ignores_pause_and_bad_marks(agent):
    agent.set_state("paused", "")
    agent.set_state("backend.bad", f"{DIGEST_2}\n")
    agent.rules = [*registry(DIGEST_2, tag=f"sha-{SHA_B}"), health("ok")]

    result = agent.mcpwr("backend", "deploy", SHA_B)

    assert result.returncode == 0, result.stderr
    assert agent.state("backend.deployed") == f"{SHA_B} {DIGEST_2}"
    assert agent.state("backend.bad") is None
    assert (agent.home / "state/paused").exists()


@pytest.mark.parametrize("arg", ["main", "abc123", "../x", "A" * 40])
def test_manual_deploy_needs_a_full_sha(agent, arg):
    result = agent.mcpwr("backend", "deploy", arg)

    assert result.returncode == 2
    assert agent.calls() == []


def test_manual_rollback_swaps_releases_without_migrating(agent):
    agent.set_state("backend.deployed", f"{SHA_B} {DIGEST_2}\n")
    agent.set_state("backend.previous", f"{SHA_A} {DIGEST_1}\n")
    agent.mark_fetched(SHA_A)
    agent.rules = [health("ok"), SERVICES]

    result = agent.mcpwr("backend", "rollback")

    assert result.returncode == 0, result.stderr
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"
    assert agent.state("backend.previous") == f"{SHA_B} {DIGEST_2}"
    assert os.readlink(agent.home / "stacks/backend/current") == f"releases/{SHA_A}"
    assert all("migrate" not in compose_action(c) for c in agent.compose())
    assert agent.state("backend.bad") == DIGEST_2


def test_rollback_then_tick_is_a_no_op(agent):
    # :main still points at the release that was rolled back from: it stays rolled back.
    agent.set_state("backend.deployed", f"{SHA_B} {DIGEST_2}\n")
    agent.set_state("backend.previous", f"{SHA_A} {DIGEST_1}\n")
    agent.mark_fetched(SHA_A)
    agent.rules = [health("ok"), SERVICES, *registry(DIGEST_2)]
    assert agent.mcpwr("backend", "rollback").returncode == 0
    compose_calls = len(agent.compose())

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert len(agent.compose()) == compose_calls
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"



def test_manual_rollback_after_an_automatic_one_keeps_main_marked_bad(agent):
    # :main (DIGEST_3) failed its gate and was rolled back to SHA_B automatically; the operator
    # then rolls back one step further. The next tick must still leave :main alone.
    agent.set_state("backend.deployed", f"{SHA_B} {DIGEST_2}\n")
    agent.set_state("backend.previous", f"{SHA_A} {DIGEST_1}\n")
    agent.set_state("backend.bad", f"{DIGEST_3}\n")
    agent.mark_fetched(SHA_A)
    agent.rules = [health("ok"), SERVICES, *registry(DIGEST_3), revision(DIGEST_3, SHA_C)]
    assert agent.mcpwr("backend", "rollback").returncode == 0
    compose_calls = len(agent.compose())

    result = agent.tick()

    assert result.returncode == 0, result.stderr
    assert len(agent.compose()) == compose_calls
    assert agent.state("backend.deployed") == f"{SHA_A} {DIGEST_1}"


def test_status_lists_every_bad_digest(agent):
    agent.set_state("backend.bad", f"{DIGEST_1}\n{DIGEST_2}\n")

    result = agent.mcpwr("backend", "status")

    assert result.returncode == 0, result.stderr
    assert f"bad:      {DIGEST_1} {DIGEST_2}" in result.stdout.splitlines()

@pytest.mark.parametrize("command", [["deploy", SHA_B], ["rollback"]])
def test_manual_command_waits_for_the_lock_and_fails_if_it_never_comes(agent, command):
    agent.set_state("backend.deployed", f"{SHA_B} {DIGEST_2}\n")
    agent.set_state("backend.previous", f"{SHA_A} {DIGEST_1}\n")
    agent.rules = [{"cmd": "flock", "exit": 1}]

    result = agent.mcpwr("backend", *command)

    assert result.returncode == 1
    assert "another deploy still holds the lock" in result.stderr
    assert [c["args"] for c in agent.calls("flock")] == [["-w", "900", "9"]]
    assert agent.calls("curl") == []
    assert agent.compose() == []


def test_rollback_without_a_previous_release_fails(agent):
    result = agent.mcpwr("backend", "rollback")

    assert result.returncode == 1
    assert "no previous release" in result.stderr
    assert agent.compose() == []


def test_pause_and_resume(agent):
    assert agent.mcpwr("pause").returncode == 0
    assert (agent.home / "state/paused").exists()

    assert agent.mcpwr("resume").returncode == 0
    assert not (agent.home / "state/paused").exists()


def test_proxies_lists_peers_that_forward_for_someone(agent):
    log = "\n".join(
        [
            '10.0.0.5 - - [23/Sep/2026:10:00:00 +0000] "GET /health/live HTTP/1.1" 200 2 "-" '
            '"curl" rid=a rt=0.001 urt=0.001 peer=10.0.0.5 xff="-"',
            '203.0.113.7 - - [23/Sep/2026:10:00:01 +0000] "GET / HTTP/1.1" 200 5 "-" "Mozilla" '
            'rid=b rt=0.010 urt=0.010 peer=10.21.0.9 xff="203.0.113.7"',
            '198.51.100.2 - - [23/Sep/2026:10:00:02 +0000] "GET / HTTP/1.1" 200 5 "-" "Mozilla" '
            'rid=c rt=0.010 urt=0.010 peer=10.21.0.9 xff="198.51.100.2"',
        ]
    )
    agent.rules = [
        {"cmd": "docker", "match": r"^ps -q ", "stdout": "cid123\n"},
        {"cmd": "docker", "match": r"^logs --since 24h cid123$", "stdout": log + "\n"},
    ]

    result = agent.mcpwr("backend", "proxies")

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["2", "10.21.0.9"]
    assert [
        "ps", "-q",
        "--filter", "label=com.docker.compose.project=backend-mcp",
        "--filter", "label=com.docker.compose.service=nginx",
    ] in [c["args"] for c in agent.calls("docker")]
