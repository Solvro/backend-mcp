"""Runs the shell scripts under deploy/ against tests/deploy/stub.py (see its docstring)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "deploy" / "agent" / "mcpwr-deploy"
STUB = Path(__file__).with_name("stub.py")
STUBBED = ("docker", "git", "curl", "flock", "sleep")

SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40
DIGEST_1, DIGEST_2, DIGEST_3 = (f"sha256:{d * 64}" for d in "123")


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.rules: list[dict] = []
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in STUBBED:
            wrapper = self.bin / name
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" {name} "$@"\n')
            wrapper.chmod(0o755)
        self.log = root / "calls.jsonl"
        self.home = root / "home"
        self.home.mkdir()

    def run(self, script: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        rules = self.root / "rules.json"
        rules.write_text(json.dumps(self.rules))
        (self.root / "rule-counts.json").unlink(missing_ok=True)
        base = {k: v for k, v in os.environ.items() if k != "RELEASE_TAG"}
        base.update(
            PATH=f"{self.bin}:{os.environ['PATH']}",
            STUB_LOG=str(self.log),
            STUB_RULES=str(rules),
            MCPWR_HOME=str(self.home),
        )
        base.update(env)
        return subprocess.run(
            ["bash", str(script), *args], env=base, capture_output=True, text=True, timeout=60
        )

    def calls(self, cmd: str | None = None) -> list[dict]:
        if not self.log.exists():
            return []
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [e for e in entries if cmd is None or e["cmd"] == cmd]


class Agent(Sandbox):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.conf_dir = root / "stacks-conf"
        self.conf_dir.mkdir()

    def write_conf(self, stack: str, text: str) -> None:
        (self.conf_dir / f"{stack}.conf").write_text(text)

    def mcpwr(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run(
            AGENT,
            *args,
            MCPWR_STACKS_DIR=str(self.conf_dir),
            MCPWR_REGISTRY="https://registry.test",
            MCPWR_IMAGE_HOST="ghcr.io",
            MCPWR_HEALTH_BASE="http://edge.test",
            MCPWR_HEALTH_ATTEMPTS="2",
        )

    def tick(self, stack: str = "backend") -> subprocess.CompletedProcess[str]:
        return self.mcpwr(stack, "tick")

    def state(self, name: str) -> str | None:
        path = self.home / "state" / name
        return path.read_text().strip() if path.exists() else None

    def set_state(self, name: str, text: str) -> None:
        path = self.home / "state" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def compose(self) -> list[dict]:
        return [c for c in self.calls("docker") if c["args"][:1] == ["compose"]]

    def release(self, sha: str, stack: str = "backend") -> Path:
        return self.home / "stacks" / stack / "releases" / sha

    def mark_fetched(self, sha: str, stack: str = "backend") -> Path:
        path = self.release(sha, stack)
        path.mkdir(parents=True, exist_ok=True)
        (path / ".mcpwr-ok").touch()
        return path


def stack_conf(**overrides: str) -> str:
    values = {
        "REPO_URL": "https://example.invalid/backend-mcp.git",
        "SPARSE_PATHS": "docker gateway",
        "PROJECT": "backend-mcp",
        "PRIMARY_IMAGE": "solvro/backend-mcp-auth",
        "IMAGES": "solvro/backend-mcp-auth solvro/backend-mcp-chat",
        "COMPOSE_FILES": "docker/compose.yml docker/compose.prod.yml",
        "ENV_FILE": "$MCPWR_HOME/stacks/backend/.env.prod",
        "REQUIRED_NETWORKS": "solvro-mcp-internal",
        "MIGRATE_SERVICE": "migrate",
        "HEALTH_URL": "/health",
    }
    values.update(overrides)
    return "".join(f'{key}="{value}"\n' for key, value in values.items())


def registry(digest: str, tag: str = "main", image: str = "solvro/backend-mcp-auth") -> list[dict]:
    return [
        {
            "cmd": "curl",
            "match": rf"/token\?scope=repository:{re.escape(image)}:pull$",
            "stdout": '{"token":"t"}',
        },
        {
            "cmd": "curl",
            "match": rf"/v2/{re.escape(image)}/manifests/{re.escape(tag)}$",
            "stdout": f"HTTP/2 200\r\ndocker-content-digest: {digest}\r\n\r\n",
        },
    ]


def revision(digest: str, sha: str, image: str = "solvro/backend-mcp-auth") -> dict:
    return {
        "cmd": "docker",
        "match": rf"^image inspect .* ghcr\.io/{re.escape(image)}@{re.escape(digest)}$",
        "stdout": f"{sha}\n",
    }


def health(status: str = "ok", *, exit: int = 0, times: int | None = None) -> dict:
    rule = {
        "cmd": "curl",
        "match": r"http://edge\.test/health$",
        "stdout": json.dumps({"status": status}),
        "exit": exit,
    }
    if times is not None:
        rule["times"] = times
    return rule


def compose_action(call: dict) -> list[str]:
    """The compose subcommand and its arguments, i.e. everything after the last `-f <file>`."""
    args = call["args"]
    last_f = max(i for i, arg in enumerate(args) if arg == "-f")
    return args[last_f + 2 :]
