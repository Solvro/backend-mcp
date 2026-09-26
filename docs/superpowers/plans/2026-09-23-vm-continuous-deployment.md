# Production VM + Continuous Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every merge to `main` in backend-mcp, ml-mcp and frontend-mcp that passes CI reaches the
mcpwr VM (`10.21.36.20`) automatically, health-gated with automatic rollback, and the VM itself is
prepared from scripts in git.

**Architecture:** GitHub-hosted CI publishes images to GHCR (`sha-<commit>` immutable, `main`
moving, moved last). On the VM a bash agent (`mcpwr-deploy`), run every minute by a systemd timer
per stack, notices a new `main` digest, checks out that commit's compose files, pulls, migrates,
swaps with `compose up --wait`, gates on health, and rolls back to the previous release on failure.
nginx (backend stack) is the only ingress: plain HTTP on :80 behind Solvro's Coolify proxy, serving
the Next.js frontend publicly and `/health*`; backend APIs are internal-only.

**Tech Stack:** bash (3.2-compatible), systemd, Docker Engine + compose v2 (≥ 2.24 for `!override`/`!reset`), nginx 1.27, GitHub Actions (`docker/build-push-action@v6`), GHCR, pytest (+ a PATH-stub harness for shell scripts), Next.js 16 standalone output.

**Spec:** `docs/superpowers/specs/2026-09-23-vm-continuous-deployment-design.md` — read it first; this plan implements it section by section and cites it as §N.

## Global Constraints

- Images: `ghcr.io/solvro/backend-mcp-auth`, `ghcr.io/solvro/backend-mcp-chat`, `ghcr.io/solvro/ml-mcp-server`, `ghcr.io/solvro/frontend-mcp`.
- Tags: `sha-<full 40-char commit sha>` (immutable) and `main` (moving; moved only after every `sha-` image of that commit is pushed).
- Every image carries labels `org.opencontainers.image.revision=<sha>` and `org.opencontainers.image.source=<repo URL>`. Platform `linux/amd64` only.
- Compose project names on the VM: `backend-mcp`, `ml-mcp`, `frontend-mcp`. The backend's dev `docker/compose.yml` keeps `name: ml-mcp-backend`.
- Networks: `backend-mcp_backend` (subnet `10.89.0.0/24`, dynamic addresses only from `ip_range 10.89.0.128/25`; fixed: nginx `10.89.0.10`, frontend `10.89.0.11`), `solvro-mcp-internal` (`--internal`, created by the bootstrap).
- `FORWARDED_ALLOW_IPS=10.89.0.10,10.89.0.11` on auth-service and chat-service in production.
- Production nginx publishes `80:80` only; `TRUSTED_PROXY_CIDR` defaults to `127.0.0.1/32`.
- Paths: `/opt/mcpwr` (root-owned), `/opt/mcpwr/stacks` and `/opt/mcpwr/state` (owned by `mcpwr-deploy`), `/etc/ml-mcp/secrets` (0750 root:mcpwr-deploy, files 0440), `/var/backups/mcpwr` (0700 root).
- Agent: runs as system user `mcpwr-deploy`; depends only on `docker`, `git`, `curl`, `jq`, `flock`; **bash 3.2 compatible** (the tests run it with macOS `/bin/bash`): no associative arrays, no `mapfile`, no `${x,,}`, no arrays at all (space-separated strings; no paths contain spaces).
- Agent timings: `--wait-timeout 180`; health gate 10 attempts × 3 s; keep 5 releases.
- Secrets: never printed (scripts print file names only), never committed, never pasted into chat by the implementer — the human fills external API keys with `sudoedit` on the VM.
- Commit messages must pass the repo's pre-commit hook: `^(chore|ci|junk|test|feat|fix|build|docs|refactor)!?(\([a-z]+\))?: ` then a lowercase word that does not end in `ed`/`ing` (scope letters only — `(deploy)`, not `(vm-cd)`). End every commit with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on a branch `feat/vm-cd` in each repo; never commit to `main`.

## Review Focus

1. **Rollback after a release whose migration already ran.** The old `migrate` one-shot would run `alembic upgrade head` against a schema that is ahead of it and fail on the unknown revision, taking auth-service down with it. Expected: rollback never runs `migrate` (`up --no-deps` over every other service). Pinned by Task A3 `test_failed_health_gate_rolls_back_without_rerunning_migrations`.
2. **Transient infrastructure failures** — registry unreachable, image pull fails, a required network does not exist yet on first boot. Expected: the running stack is untouched, the release is *not* marked bad, and the next tick retries. Pinned by Task A1 `test_registry_failure_changes_nothing`, Task A2 `test_missing_network_is_retried_not_marked_bad` and `test_failed_pull_is_retried_not_marked_bad`.
3. **A client-supplied `X-Forwarded-For`** from a peer that is not the trusted proxy, or a forged chain through the trusted one. Expected: never becomes the client IP seen by the frontend and services. Pinned by Task A7 `test_untrusted_peer_cannot_choose_the_client_ip` and `test_client_supplied_chain_is_collapsed_to_one_address`.
4. **Re-running `init-secrets.sh`** on a live VM (someone follows the runbook twice). Expected: no existing secret or env file changes — rotating a database password would lock the services out of their own data. Pinned by Task A9 `test_rerun_changes_nothing`.
5. **An image whose `revision` label is not a commit sha** (empty, `v1.0`, `../../etc`). Expected: refused before anything is checked out or run; it must never become a path under `releases/`. Pinned by Task A2 `test_revision_that_is_not_a_sha_is_refused`.

## File Structure

**backend-mcp** (Part A)

| Path | Responsibility |
|---|---|
| `deploy/agent/mcpwr-deploy` | the deploy agent: detect → pre-flight → migrate → swap → gate → record / roll back; manual commands |
| `deploy/agent/stacks/{backend,ml-mcp,frontend}.conf` | per-stack agent settings (sourced shell) |
| `deploy/systemd/mcpwr-deploy@.service`, `mcpwr-deploy@.timer` | one tick per stack per minute |
| `deploy/systemd/mcpwr-backup.service`, `mcpwr-backup.timer` | nightly backups |
| `deploy/vm/bootstrap.sh` | idempotent VM provisioning |
| `deploy/vm/init-secrets.sh` | one-time secret + env-file creation, never overwrites |
| `deploy/vm/backup.sh` | Postgres + Mongo dumps with retention |
| `gateway/nginx/templates.proxy/{default.conf,proxy_common.inc,cors_origins.map}.template` | production edge behind Coolify |
| `gateway/nginx/nginx.conf` | log format gains `peer=`/`xff=` |
| `docker/compose.prod.yml` | pull GHCR images, proxy edge, trusted IPs |
| `.env.prod.example`, `justfile`, `scripts/check_prod_secrets.py` | prod config, `-p backend-mcp`, `RELEASE_TAG` |
| `.github/workflows/main.yaml` | `publish` job; shellcheck step |
| `tests/deploy/stub.py`, `tests/deploy/deploy_harness.py`, `tests/deploy/conftest.py` | PATH-stub harness for the shell scripts |
| `tests/deploy/test_deploy_*.py` | tests per script / concern |
| `docs/deploy.md` | production runbook |

**ml-mcp** (Part B): `docker/compose.prod.yml`, `.env.prod.example`, `tests/test_compose_prod.py`, `.github/workflows/main.yaml` (publish job).

**frontend-mcp** (Part C): `next.config.ts`, `Dockerfile`, `.dockerignore`, `deploy/compose.yml`, `.github/workflows/ci.yml`.

**VM** (Part D): no files — the bring-up runbook executed with the human on the VPN.

## Execution Order

1. **Part A** (backend-mcp PR) → merge → backend images on GHCR. A Solvro package admin makes the packages public (spec P3).
2. **Part D, Tasks D1–D5** need only Part A merged → **checkpoint: `http://10.21.36.20/health/live` answers → tell devops.**
3. **Parts B and C** (ml-mcp and frontend-mcp PRs) in parallel; once merged, the agent deploys them on its own.
4. **Part D, Tasks D6–D9**.

Spec prerequisites handled outside this plan: P1 (BFF forwards `X-Forwarded-For` on `/bff/auth/*`) and P2 (frontend `/auth/verify`, `/auth/reset-password` pages) are frontend tickets; P5 (SMTP) and P6 (e2e required in branch protection) are admin tasks listed in Part D.

---

# Part A — backend-mcp (branch `feat/vm-cd`)

### Task A1: Stub harness + agent skeleton that decides whether to deploy

Spec §7 steps 1–2. The agent is exercised with `docker`, `git`, `curl`, `flock` and `sleep`
replaced on `PATH` by a Python stub that records every call and answers from scripted rules.

**Files:**
- Create: `tests/deploy/stub.py`, `tests/deploy/deploy_harness.py`, `tests/deploy/conftest.py`
- Create: `deploy/agent/mcpwr-deploy`
- Test: `tests/deploy/test_deploy_agent_tick.py`

**Interfaces:**
- Produces (harness, used by every later `tests/deploy` task):
  - `Sandbox(root: Path)` with `.rules: list[dict]`, `.run(script: Path, *args, **env) -> CompletedProcess[str]`, `.calls(cmd: str | None = None) -> list[dict]` (each `{"cmd", "args", "release_tag"}`), `.home: Path`.
  - `Agent(Sandbox)` with `.write_conf(stack, text)`, `.mcpwr(*args)`, `.tick(stack="backend")`, `.state(name) -> str | None` (stripped), `.set_state(name, text)`, `.compose() -> list[dict]`, `.release(sha) -> Path`, `.mark_fetched(sha)`.
  - `stack_conf(**overrides) -> str`, `registry(digest, tag="main", image="solvro/backend-mcp-auth") -> list[dict]`, `revision(digest, sha, image=...) -> dict`, `health(status="ok", *, exit=0, times=None) -> dict`, `compose_action(call) -> list[str]`.
  - Constants `SHA_A/B/C`, `DIGEST_1/2/3`, `ROOT`, `AGENT`.
  - Fixtures `agent` (backend conf written) and `sandbox`.
- Produces (agent): CLI `mcpwr-deploy <stack> tick`; exit 0 = nothing to do or done, 1 = failed/retry, 2 = usage. Log lines on stderr prefixed `<6>` info, `<4>` warning, `<3>` error.

- [ ] **Step 1: Write the stub**

`tests/deploy/stub.py`:

```python
"""Stand-in for docker/git/curl/flock/sleep in the tests of the scripts under deploy/.

Invoked as `stub.py <name> <args...>` by the wrappers deploy_harness.py puts on PATH. Every call
is appended to $STUB_LOG as one JSON line. The first rule in $STUB_RULES whose `cmd` equals the
name and whose `match` regex is found in the space-joined arguments decides stdout and the exit
status (a rule with `times` stops matching after that many uses). No rule: exit 0, no output.
"""

import json
import os
import re
import sys
from pathlib import Path

name, args = sys.argv[1], sys.argv[2:]
joined = " ".join(args)

with Path(os.environ["STUB_LOG"]).open("a") as log:
    record = {"cmd": name, "args": args, "release_tag": os.environ.get("RELEASE_TAG")}
    log.write(json.dumps(record) + "\n")

rules_path = Path(os.environ["STUB_RULES"])
counts_path = rules_path.with_name("rule-counts.json")
rules = json.loads(rules_path.read_text())
counts = json.loads(counts_path.read_text()) if counts_path.exists() else {}

for index, rule in enumerate(rules):
    if rule["cmd"] != name or not re.search(rule.get("match", ""), joined):
        continue
    used = counts.get(str(index), 0)
    if "times" in rule and used >= rule["times"]:
        continue
    counts[str(index)] = used + 1
    counts_path.write_text(json.dumps(counts))
    sys.stdout.write(rule.get("stdout", ""))
    sys.exit(rule.get("exit", 0))

sys.exit(0)
```

- [ ] **Step 2: Write the harness**

`tests/deploy/deploy_harness.py`:

```python
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
```

`tests/deploy/conftest.py`:

```python
import shutil

import pytest
from deploy_harness import Agent, Sandbox, stack_conf


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


@pytest.fixture
def agent(tmp_path) -> Agent:
    if shutil.which("jq") is None:
        pytest.skip("the deploy agent needs jq")
    a = Agent(tmp_path)
    a.write_conf("backend", stack_conf())
    return a
```

- [ ] **Step 3: Write the failing tests**

`tests/deploy/test_deploy_agent_tick.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_agent_tick.py -v --no-cov`
Expected: all 6 FAIL — `bash: .../deploy/agent/mcpwr-deploy: No such file or directory` (return code 127).

- [ ] **Step 5: Write the agent skeleton**

`deploy/agent/mcpwr-deploy` (then `chmod +x deploy/agent/mcpwr-deploy`):

```bash
#!/usr/bin/env bash
# mcpwr-deploy - pull-based deploy agent for the mcpwr VM.
# Design: docs/superpowers/specs/2026-09-23-vm-continuous-deployment-design.md (section 7).
#
#   mcpwr-deploy <stack> tick            what the systemd timer runs every minute
#
# Runs as mcpwr-deploy. Kept bash 3.2 compatible (no arrays): the tests run it on macOS.
set -euo pipefail

MCPWR_HOME="${MCPWR_HOME:-/opt/mcpwr}"
MCPWR_STACKS_DIR="${MCPWR_STACKS_DIR:-$MCPWR_HOME/agent/stacks}"
MCPWR_REGISTRY="${MCPWR_REGISTRY:-https://ghcr.io}"
MCPWR_IMAGE_HOST="${MCPWR_IMAGE_HOST:-ghcr.io}"
MCPWR_HEALTH_BASE="${MCPWR_HEALTH_BASE:-http://127.0.0.1}"
MCPWR_WAIT_TIMEOUT="${MCPWR_WAIT_TIMEOUT:-180}"
MCPWR_HEALTH_ATTEMPTS="${MCPWR_HEALTH_ATTEMPTS:-10}"
MCPWR_KEEP_RELEASES="${MCPWR_KEEP_RELEASES:-5}"
STATE="$MCPWR_HOME/state"

# journald reads a leading <N> as the syslog priority.
info() { printf '<6>%s\n' "$*" >&2; }
warn() { printf '<4>%s\n' "$*" >&2; }
err() { printf '<3>%s\n' "$*" >&2; }

# The one place a GitHub Deployments integration hooks in later (spec F1).
report() { # <stack> <event> <sha> [detail]
  info "report stack=$1 event=$2 sha=$3${4:+ detail=$4}"
}

usage() {
  cat >&2 <<'EOF'
usage: mcpwr-deploy <stack> tick
EOF
  exit 2
}

load_stack() { # <stack>
  STACK="$1"
  local conf="$MCPWR_STACKS_DIR/$STACK.conf"
  [ -f "$conf" ] || { err "unknown stack: $STACK"; exit 2; }
  REPO_URL="" SPARSE_PATHS="" PROJECT="" PRIMARY_IMAGE="" IMAGES="" COMPOSE_FILES=""
  ENV_FILE="" REQUIRED_NETWORKS="" MIGRATE_SERVICE="" HEALTH_URL=""
  # shellcheck source=/dev/null
  . "$conf"
  STACK_DIR="$MCPWR_HOME/stacks/$STACK"
  mkdir -p "$STACK_DIR/releases" "$STATE"
}

with_lock() {
  exec 9>"$STATE/deploy.lock"
  if ! flock -n 9; then
    info "another deploy holds the lock; skipping"
    exit 0
  fi
}

state_read() { # <file> -> its content, or nothing
  if [ -f "$STATE/$1" ]; then cat "$STATE/$1"; fi
}

registry_digest() { # <image path, e.g. solvro/backend-mcp-auth> <tag> -> digest
  local image="$1" tag="$2" token
  token=$(curl -fsS "$MCPWR_REGISTRY/token?scope=repository:$image:pull" | jq -r '.token') || return 1
  curl -fsSI \
    -H "Authorization: Bearer $token" \
    -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json" \
    "$MCPWR_REGISTRY/v2/$image/manifests/$tag" |
    tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }'
}

deploy_and_handle() { # <sha> <digest> - replaced in Task A2
  err "deploying is not implemented yet"
  return 1
}

resolve_sha() { # <digest> - replaced in Task A2
  return 1
}

cmd_tick() {
  with_lock
  if [ -f "$STATE/paused" ]; then
    info "automatic deploys are paused; not deploying $STACK"
    return 0
  fi
  local digest deployed bad sha
  digest=$(registry_digest "$PRIMARY_IMAGE" main) || digest=""
  if [ -z "$digest" ]; then
    warn "could not resolve $PRIMARY_IMAGE:main"
    return 1
  fi
  deployed=$(state_read "$STACK.deployed")
  bad=$(state_read "$STACK.bad")
  if [ "${deployed#* }" = "$digest" ] || [ "$bad" = "$digest" ]; then
    return 0
  fi
  sha=$(resolve_sha "$digest") || { warn "could not read the revision of $digest"; return 1; }
  deploy_and_handle "$sha" "$digest"
}

main() {
  [ $# -ge 2 ] || usage
  load_stack "$1"
  case "$2" in
    tick) cmd_tick ;;
    *) usage ;;
  esac
}

main "$@"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/test_deploy_agent_tick.py -v --no-cov`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add tests/deploy/stub.py tests/deploy/deploy_harness.py tests/deploy/conftest.py tests/deploy/test_deploy_agent_tick.py deploy/agent/mcpwr-deploy
git update-index --chmod=+x deploy/agent/mcpwr-deploy
git commit -m "feat(deploy): tick detection for the pull-based deploy agent" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A2: Deploy a new release — pre-flight, migrate, swap, health gate, record, prune

Spec §7 steps 3–7.

**Files:**
- Modify: `deploy/agent/mcpwr-deploy` (replace the two Task A1 placeholders; add functions)
- Test: `tests/deploy/test_deploy_agent_release.py`

**Interfaces:**
- Consumes: harness from Task A1.
- Produces (agent internals used by Tasks A3–A4): `resolve_sha <digest>` (prints a 40-hex sha or fails), `fetch_release <sha>`, `compose <release_dir> <sha> <args...>` (runs `RELEASE_TAG=sha-<sha> docker compose -p $PROJECT [--env-file $ENV_FILE] -f <dir>/<file>...`), `health_gate`, `record_success <sha> <digest>`, `prune_releases`, exit-code constants `PREFLIGHT=10`, `MIGRATE_FAILED=11`, `SWAP_FAILED=20`, and `deploy_release <sha>` returning one of them or 0.
- State file formats: `<stack>.deployed` and `<stack>.previous` = `"<sha> <digest>\n"`; `<stack>.bad` = `"<digest>\n"`; release checkout complete when `releases/<sha>/.mcpwr-ok` exists.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_agent_release.py`:

```python
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
    assert ["network", "inspect", "backend-mcp_backend"] in [c["args"] for c in agent.calls("docker")]
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_agent_release.py -v --no-cov`
Expected: 7 FAIL (`deploying is not implemented yet`, or missing state/calls). The 4 cases of
`test_revision_that_is_not_a_sha_is_refused` already pass, because Task A1's placeholder
`resolve_sha` refuses every digest; they start guarding real behaviour in Step 3.

- [ ] **Step 3: Implement**

In `deploy/agent/mcpwr-deploy`, add after the `MCPWR_KEEP_RELEASES=` line:

```bash
# deploy_release outcomes
PREFLIGHT=10      # nothing running changed; retry next tick
MIGRATE_FAILED=11 # nothing swapped; the release itself is broken
SWAP_FAILED=20    # the new release is (partly) running and failed
```

Delete the placeholder `deploy_and_handle` and `resolve_sha` functions and insert, in their place:

```bash
resolve_sha() { # <digest> -> the commit sha the image was built from
  local ref="$MCPWR_IMAGE_HOST/$PRIMARY_IMAGE@$1" sha
  docker pull -q "$ref" >/dev/null || return 1
  sha=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$ref") || return 1
  # The sha becomes a directory name and a git ref: accept nothing but 40 lowercase hex digits.
  printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || return 1
  printf '%s\n' "$sha"
}

fetch_release() { # <sha>: shallow sparse checkout of that commit into releases/<sha>
  local sha="$1" dir="$STACK_DIR/releases/$1"
  [ -f "$dir/.mcpwr-ok" ] && return 0
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q || return 1
  git -C "$dir" remote add origin "$REPO_URL" || return 1
  # shellcheck disable=SC2086 # SPARSE_PATHS is a space-separated list
  git -C "$dir" sparse-checkout set $SPARSE_PATHS || return 1
  git -C "$dir" fetch -q --depth 1 origin "$sha" || return 1
  git -C "$dir" checkout -q FETCH_HEAD || return 1
  touch "$dir/.mcpwr-ok"
}

compose() { # <release dir> <sha> <compose args...>
  local dir="$1" sha="$2" files="" env_file="" f
  shift 2
  for f in $COMPOSE_FILES; do files="$files -f $dir/$f"; done
  if [ -n "$ENV_FILE" ]; then env_file="--env-file $ENV_FILE"; fi
  # shellcheck disable=SC2086 # word splitting of $env_file/$files is intended; no paths contain spaces
  RELEASE_TAG="sha-$sha" docker compose -p "$PROJECT" $env_file $files "$@"
}

health_gate() {
  [ -n "$HEALTH_URL" ] || return 0
  local attempt=1 body="" status=""
  while [ "$attempt" -le "$MCPWR_HEALTH_ATTEMPTS" ]; do
    if body=$(curl -fsS -m 10 "$MCPWR_HEALTH_BASE$HEALTH_URL"); then
      status=$(printf '%s' "$body" | jq -r '.status // empty' 2>/dev/null || true)
      case "$status" in ok | degraded) return 0 ;; esac
    fi
    attempt=$((attempt + 1))
    sleep 3
  done
  err "health gate failed on $HEALTH_URL (last status: ${status:-none})"
  return 1
}

deploy_release() { # <sha> -> 0, PREFLIGHT, MIGRATE_FAILED or SWAP_FAILED
  local sha="$1" dir="$STACK_DIR/releases/$1" network
  fetch_release "$sha" || { warn "fetching $sha from $REPO_URL failed"; return "$PREFLIGHT"; }
  for network in $REQUIRED_NETWORKS; do
    docker network inspect "$network" >/dev/null 2>&1 ||
      { warn "required network $network does not exist yet"; return "$PREFLIGHT"; }
  done
  compose "$dir" "$sha" pull --quiet || { warn "pulling sha-$sha failed"; return "$PREFLIGHT"; }
  if [ -n "$MIGRATE_SERVICE" ]; then
    compose "$dir" "$sha" run --rm "$MIGRATE_SERVICE" ||
      { err "migration of $sha failed"; return "$MIGRATE_FAILED"; }
  fi
  compose "$dir" "$sha" up -d --no-build --remove-orphans --wait --wait-timeout "$MCPWR_WAIT_TIMEOUT" ||
    return "$SWAP_FAILED"
  health_gate || return "$SWAP_FAILED"
}

prune_releases() { # keep the newest MCPWR_KEEP_RELEASES, and always current + previous
  local current previous old image
  current=$(state_read "$STACK.deployed")
  current="${current%% *}"
  previous=$(state_read "$STACK.previous")
  previous="${previous%% *}"
  # shellcheck disable=SC2012 # names are 40-hex shas
  ls -1t "$STACK_DIR/releases" | tail -n +"$((MCPWR_KEEP_RELEASES + 1))" | while read -r old; do
    if [ "$old" = "$current" ] || [ "$old" = "$previous" ]; then continue; fi
    rm -rf "${STACK_DIR:?}/releases/$old"
    for image in $IMAGES; do
      docker image rm "$MCPWR_IMAGE_HOST/$image:sha-$old" >/dev/null 2>&1 || true
    done
    info "pruned release $old"
  done
}

record_success() { # <sha> <digest>
  local sha="$1" digest="$2" current
  current=$(state_read "$STACK.deployed")
  if [ -n "$current" ] && [ "${current%% *}" != "$sha" ]; then
    printf '%s\n' "$current" >"$STATE/$STACK.previous"
  fi
  printf '%s %s\n' "$sha" "$digest" >"$STATE/$STACK.deployed"
  rm -f "$STATE/$STACK.bad"
  touch "$STACK_DIR/releases/$sha"
  ln -sfn "releases/$sha" "$STACK_DIR/current"
  prune_releases
}

deploy_and_handle() { # <sha> <digest>
  local sha="$1" digest="$2" rc=0
  report "$STACK" started "$sha"
  deploy_release "$sha" || rc=$?
  if [ "$rc" -eq 0 ]; then
    record_success "$sha" "$digest"
    report "$STACK" success "$sha"
    return 0
  fi
  if [ "$rc" -eq "$PREFLIGHT" ]; then
    report "$STACK" preflight-failed "$sha" "will retry"
    return 1
  fi
  err "deploy of $sha failed (code $rc)"
  return 1
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/ -v --no-cov`
Expected: all Task A1 and A2 tests pass (17 passed).

- [ ] **Step 5: Commit**

```bash
git add deploy/agent/mcpwr-deploy tests/deploy/test_deploy_agent_release.py
git commit -m "feat(deploy): pre-flight, migrate, swap and health gate in the deploy agent" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A3: Failures — bad marks and automatic rollback (never re-running migrations)

Spec §7 step 8 and Review Focus 1. A migration failure marks the release bad without touching the
running release (retrying a deterministic failure every minute would only hammer the database).
A swap or health-gate failure marks it bad and redeploys the release that was running, with
`up --no-deps` over every service except `migrate`.

**Files:**
- Modify: `deploy/agent/mcpwr-deploy` (replace `deploy_release` and `deploy_and_handle`)
- Test: `tests/deploy/test_deploy_agent_rollback.py`

**Interfaces:**
- Consumes: Task A2 functions and constants.
- Produces: `deploy_release <sha> <forward|rollback>` (rollback: no migrate, `config --services` then `up -d --no-build --no-deps --wait --wait-timeout 180 <services except MIGRATE_SERVICE>`); used by Task A4's `rollback` command.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_agent_rollback.py`:

```python
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
        ["pull", "--quiet"],
        ["config", "--services"],
        [
            "up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "180",
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_agent_rollback.py -v --no-cov`
Expected: the first five FAIL (no bad mark / no rollback yet); `test_new_main_after_a_bad_release_deploys_and_clears_the_mark` already passes (Task A2's `record_success` clears the mark).

- [ ] **Step 3: Implement**

In `deploy/agent/mcpwr-deploy`, replace the whole `deploy_release` function with:

```bash
deploy_release() { # <sha> <forward|rollback> -> 0, PREFLIGHT, MIGRATE_FAILED or SWAP_FAILED
  local sha="$1" mode="$2" dir="$STACK_DIR/releases/$1" network services
  fetch_release "$sha" || { warn "fetching $sha from $REPO_URL failed"; return "$PREFLIGHT"; }
  for network in $REQUIRED_NETWORKS; do
    docker network inspect "$network" >/dev/null 2>&1 ||
      { warn "required network $network does not exist yet"; return "$PREFLIGHT"; }
  done
  compose "$dir" "$sha" pull --quiet || { warn "pulling sha-$sha failed"; return "$PREFLIGHT"; }
  if [ "$mode" = forward ]; then
    if [ -n "$MIGRATE_SERVICE" ]; then
      compose "$dir" "$sha" run --rm "$MIGRATE_SERVICE" ||
        { err "migration of $sha failed"; return "$MIGRATE_FAILED"; }
    fi
    compose "$dir" "$sha" up -d --no-build --remove-orphans --wait --wait-timeout "$MCPWR_WAIT_TIMEOUT" ||
      return "$SWAP_FAILED"
  else
    # A rollback never re-runs the old migrate one-shot: the schema may already be ahead of it,
    # alembic would fail on the unknown revision, and auth-service (which depends on it) with it.
    services=$(compose "$dir" "$sha" config --services) || return "$SWAP_FAILED"
    services=$(printf '%s\n' "$services" | grep -vx "${MIGRATE_SERVICE:-}" || true)
    # shellcheck disable=SC2086 # one word per service name
    compose "$dir" "$sha" up -d --no-build --no-deps --wait --wait-timeout "$MCPWR_WAIT_TIMEOUT" $services ||
      return "$SWAP_FAILED"
  fi
  health_gate || return "$SWAP_FAILED"
}
```

and replace the whole `deploy_and_handle` function with:

```bash
deploy_and_handle() { # <sha> <digest>
  local sha="$1" digest="$2" rc=0 running
  report "$STACK" started "$sha"
  deploy_release "$sha" forward || rc=$?
  case "$rc" in
    0)
      record_success "$sha" "$digest"
      report "$STACK" success "$sha"
      return 0
      ;;
    "$PREFLIGHT")
      report "$STACK" preflight-failed "$sha" "will retry"
      return 1
      ;;
    "$MIGRATE_FAILED")
      printf '%s\n' "$digest" >"$STATE/$STACK.bad"
      report "$STACK" failure "$sha" "migration failed; the running release was not touched"
      return 1
      ;;
  esac
  # The swap or the health gate failed: the new release is (partly) running.
  printf '%s\n' "$digest" >"$STATE/$STACK.bad"
  running=$(state_read "$STACK.deployed")
  if [ -z "$running" ]; then
    report "$STACK" failure "$sha" "first deploy failed; nothing to roll back to"
    return 1
  fi
  running="${running%% *}"
  err "rolling $STACK back to $running"
  if deploy_release "$running" rollback; then
    ln -sfn "releases/$running" "$STACK_DIR/current"
    report "$STACK" rolled-back "$sha" "running $running again"
  else
    err "ROLLBACK OF $STACK TO $running FAILED - manual action needed"
    report "$STACK" rollback-failed "$sha" "manual action needed"
  fi
  return 1
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/ -v --no-cov`
Expected: all pass (23 passed).

- [ ] **Step 5: Commit**

```bash
git add deploy/agent/mcpwr-deploy tests/deploy/test_deploy_agent_rollback.py
git commit -m "feat(deploy): automatic rollback that never re-runs migrations" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A4: Manual commands — status, deploy, rollback, pause/resume, proxies

Spec §7 "Manual commands" and §8 (proxy discovery).

**Files:**
- Modify: `deploy/agent/mcpwr-deploy` (new `cmd_*` functions, `usage`, `main`, header comment)
- Test: `tests/deploy/test_deploy_agent_commands.py`

**Interfaces:**
- Consumes: `deploy_release <sha> rollback`, `deploy_and_handle`, `registry_digest`, `with_lock` (Tasks A1–A3).
- Produces the CLI used by the runbook (Task A11, Part D):
  `mcpwr-deploy <stack> tick|status|rollback`, `mcpwr-deploy <stack> deploy <40-hex sha>`,
  `mcpwr-deploy <stack> proxies [since]`, `mcpwr-deploy pause|resume`. `proxies` prints
  `uniq -c` lines (`<count> <peer ip>`), most frequent first.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_agent_commands.py`:

```python
import os

import pytest
from deploy_harness import DIGEST_1, DIGEST_2, SHA_A, SHA_B, compose_action, health, registry

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
            '10.0.0.5 - - [23/Sep/2026:10:00:00 +0000] "GET /health/live HTTP/1.1" 200 2 "-" "curl" '
            'rid=a rt=0.001 urt=0.001 peer=10.0.0.5 xff="-"',
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_agent_commands.py -v --no-cov`
Expected: all FAIL with exit code 2 (`usage:`) — the commands do not exist yet.

- [ ] **Step 3: Implement**

In `deploy/agent/mcpwr-deploy`, replace the header comment block (lines 2–7) with:

```bash
# mcpwr-deploy - pull-based deploy agent for the mcpwr VM.
# Design: docs/superpowers/specs/2026-09-23-vm-continuous-deployment-design.md (section 7).
#
#   mcpwr-deploy <stack> tick                what the systemd timer runs every minute
#   mcpwr-deploy <stack> status              deployed / previous / bad / paused
#   mcpwr-deploy <stack> deploy <sha>        deploy that commit now (ignores pause and bad marks)
#   mcpwr-deploy <stack> rollback            back to the previous release, without migrating
#   mcpwr-deploy <stack> proxies [since]     peers that sent X-Forwarded-For (backend only)
#   mcpwr-deploy pause | resume              stop / restart automatic deploys of every stack
#
# Runs as mcpwr-deploy. Kept bash 3.2 compatible (no arrays): the tests run it on macOS.
```

Replace `usage` with:

```bash
usage() {
  cat >&2 <<'EOF'
usage: mcpwr-deploy <stack> tick|status|rollback
       mcpwr-deploy <stack> deploy <full commit sha>
       mcpwr-deploy <stack> proxies [since, default 24h]
       mcpwr-deploy pause|resume
EOF
  exit 2
}
```

Add before `main`:

```bash
cmd_status() {
  local value
  printf 'stack:    %s\n' "$STACK"
  value=$(state_read "$STACK.deployed")
  printf 'deployed: %s\n' "${value:--}"
  value=$(state_read "$STACK.previous")
  printf 'previous: %s\n' "${value:--}"
  value=$(state_read "$STACK.bad")
  printf 'bad:      %s\n' "${value:--}"
  if [ -f "$STATE/paused" ]; then value=yes; else value=no; fi
  printf 'paused:   %s\n' "$value"
}

cmd_deploy() { # <sha>
  local sha="$1" digest
  printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$' || { err "not a full commit sha: $sha"; exit 2; }
  with_lock
  digest=$(registry_digest "$PRIMARY_IMAGE" "sha-$sha") || digest=""
  [ -n "$digest" ] || { err "no image $PRIMARY_IMAGE:sha-$sha on the registry"; return 1; }
  deploy_and_handle "$sha" "$digest"
}

cmd_rollback() {
  local running previous
  with_lock
  previous=$(state_read "$STACK.previous")
  [ -n "$previous" ] || { err "no previous release of $STACK recorded"; return 1; }
  running=$(state_read "$STACK.deployed")
  if deploy_release "${previous%% *}" rollback; then
    printf '%s\n' "$previous" >"$STATE/$STACK.deployed"
    printf '%s\n' "$running" >"$STATE/$STACK.previous"
    ln -sfn "releases/${previous%% *}" "$STACK_DIR/current"
    report "$STACK" rolled-back "${running%% *}" "running ${previous%% *} (manual)"
  else
    err "rollback of $STACK to ${previous%% *} failed"
    return 1
  fi
}

cmd_proxies() { # <since>
  local nginx
  nginx=$(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=nginx")
  [ -n "$nginx" ] || { err "no running nginx in project $PROJECT"; return 1; }
  docker logs --since "$1" "$nginx" 2>&1 |
    sed -n 's/.* peer=\([^ ]*\) xff="\([^"]*\)".*/\1 \2/p' |
    awk '$2 != "-" && $2 != "" { print $1 }' | sort | uniq -c | sort -rn
}
```

Replace `main` with:

```bash
main() {
  [ $# -ge 1 ] || usage
  case "$1" in
    pause)
      mkdir -p "$STATE"
      touch "$STATE/paused"
      info "automatic deploys paused"
      return 0
      ;;
    resume)
      rm -f "$STATE/paused"
      info "automatic deploys resumed"
      return 0
      ;;
  esac
  [ $# -ge 2 ] || usage
  load_stack "$1"
  case "$2" in
    tick) cmd_tick ;;
    status) cmd_status ;;
    deploy)
      [ $# -eq 3 ] || usage
      cmd_deploy "$3"
      ;;
    rollback) cmd_rollback ;;
    proxies) cmd_proxies "${3:-24h}" ;;
    *) usage ;;
  esac
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/ -v --no-cov`
Expected: all pass (33 passed).

- [ ] **Step 5: Commit**

```bash
git add deploy/agent/mcpwr-deploy tests/deploy/test_deploy_agent_commands.py
git commit -m "feat(deploy): status, deploy, rollback, pause and proxies commands" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A5: Stack configs, systemd units, shellcheck in CI

Spec §5 (install targets) and §7 (per-stack config, timer).

**Files:**
- Create: `deploy/agent/stacks/backend.conf`, `deploy/agent/stacks/ml-mcp.conf`, `deploy/agent/stacks/frontend.conf`
- Create: `deploy/systemd/mcpwr-deploy@.service`, `deploy/systemd/mcpwr-deploy@.timer`
- Modify: `.github/workflows/main.yaml` (lint job), `justfile` (new `lint-sh` recipe)
- Test: `tests/deploy/test_deploy_stack_configs.py`

**Interfaces:**
- Consumes: the variable names `load_stack` resets (Task A1).
- Produces: installed paths the bootstrap (Task A11) copies: `/opt/mcpwr/agent/mcpwr-deploy`, `/opt/mcpwr/agent/stacks/*.conf`, `/etc/systemd/system/mcpwr-deploy@.{service,timer}`; timer instances `mcpwr-deploy@backend`, `@ml-mcp`, `@frontend`.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_stack_configs.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_stack_configs.py -v --no-cov`
Expected: FAIL — `No such file or directory` for the `.conf` files and units.

- [ ] **Step 3: Write the configs and units**

`deploy/agent/stacks/backend.conf`:

```bash
# mcpwr-deploy settings for Solvro/backend-mcp. Sourced by the agent, with MCPWR_HOME set.
REPO_URL="https://github.com/Solvro/backend-mcp.git"
SPARSE_PATHS="docker gateway"
PROJECT="backend-mcp"
PRIMARY_IMAGE="solvro/backend-mcp-auth"
IMAGES="solvro/backend-mcp-auth solvro/backend-mcp-chat"
COMPOSE_FILES="docker/compose.yml docker/compose.prod.yml"
ENV_FILE="$MCPWR_HOME/stacks/backend/.env.prod"
REQUIRED_NETWORKS="solvro-mcp-internal"
MIGRATE_SERVICE="migrate"
# Through nginx, so the gate also proves the edge routes. `degraded` passes (ml-mcp may be down).
HEALTH_URL="/health"
```

`deploy/agent/stacks/ml-mcp.conf`:

```bash
# mcpwr-deploy settings for Solvro/ml-mcp. Sourced by the agent, with MCPWR_HOME set.
REPO_URL="https://github.com/Solvro/ml-mcp.git"
SPARSE_PATHS="docker"
PROJECT="ml-mcp"
PRIMARY_IMAGE="solvro/ml-mcp-server"
IMAGES="solvro/ml-mcp-server"
COMPOSE_FILES="docker/compose.stack.yml docker/compose.prod.yml"
ENV_FILE="$MCPWR_HOME/stacks/ml-mcp/.env"
REQUIRED_NETWORKS="solvro-mcp-internal"
MIGRATE_SERVICE=""
# mcp-server's own healthcheck queries Neo4j; `up --wait` enforces it.
HEALTH_URL=""
```

`deploy/agent/stacks/frontend.conf`:

```bash
# mcpwr-deploy settings for Solvro/frontend-mcp. Sourced by the agent, with MCPWR_HOME set.
REPO_URL="https://github.com/Solvro/frontend-mcp.git"
SPARSE_PATHS="deploy"
PROJECT="frontend-mcp"
PRIMARY_IMAGE="solvro/frontend-mcp"
IMAGES="solvro/frontend-mcp"
COMPOSE_FILES="deploy/compose.yml"
ENV_FILE=""
# Created by the backend stack; until it exists every tick is a retried pre-flight failure.
REQUIRED_NETWORKS="backend-mcp_backend"
MIGRATE_SERVICE=""
# The container healthcheck, enforced by `up --wait`. Not gated through nginx: nginx belongs to
# the backend stack, and a backend outage must not roll back a good frontend release.
HEALTH_URL=""
```

`deploy/systemd/mcpwr-deploy@.service`:

```ini
[Unit]
Description=mcpwr deploy tick for the %i stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=mcpwr-deploy
Group=mcpwr-deploy
ExecStart=/opt/mcpwr/agent/mcpwr-deploy %i tick
# A tick that swaps (<=180 s wait), fails its gate (~30 s) and rolls back (<=180 s + ~30 s).
TimeoutStartSec=15min
```

`deploy/systemd/mcpwr-deploy@.timer`:

```ini
[Unit]
Description=Check GHCR for a new %i release every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5s

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/test_deploy_stack_configs.py -v --no-cov`
Expected: 5 passed.

- [ ] **Step 5: Add shellcheck to CI and `just`**

In `.github/workflows/main.yaml`, append to the `lint` job's `steps:` (after the `Ruff` step):

```yaml
      - name: Shellcheck deploy scripts
        run: |
          docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable \
            $(find deploy -type f \( -name '*.sh' -o -name 'mcpwr-deploy' \) | sort)
```

In `justfile`, after the `lint:` recipe:

```just
# Shell scripts under deploy/ (needs Docker for the shellcheck image).
lint-sh:
    docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable $(find deploy -type f \( -name '*.sh' -o -name 'mcpwr-deploy' \) | sort)
```

Run: `just lint-sh`
Expected: no output, exit 0. Fix any finding in `mcpwr-deploy` before continuing (the `# shellcheck disable=` comments above cover the intentional word splitting).

- [ ] **Step 6: Commit**

```bash
git add deploy/agent/stacks deploy/systemd tests/deploy/test_deploy_stack_configs.py .github/workflows/main.yaml justfile
git commit -m "feat(deploy): stack configs, systemd timer and shellcheck" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A6: Production compose pulls GHCR images under project `backend-mcp`

Spec §6 ("Compose changes") and the `backend-mcp` project name decision.

**Files:**
- Modify: `docker/compose.prod.yml`, `scripts/check_prod_secrets.py`, `justfile` (`up-prod`, `down-prod`)
- Test: `tests/deploy/test_deploy_prod_compose.py`

**Interfaces:**
- Consumes: `scripts/check_prod_secrets.py:rendered_config()` and `main()` (existing).
- Produces: `RELEASE_TAG` is required by `compose.prod.yml` (`sha-<commit>`); `just up-prod <sha>`.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_prod_compose.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_prod_compose.py -v --no-cov`
Expected: the first four FAIL (no `image:` on migrate/chat, `FORWARDED_ALLOW_IPS` is `10.89.0.10`, dev-key volumes present, no `ip_range`); `test_whole_stack_policy_check_passes` passes.

- [ ] **Step 3: Implement**

In `docker/compose.prod.yml`, add at the very top of the file:

```yaml
# Production overlay. On the VM the deploy agent runs (docs/deploy.md):
#   RELEASE_TAG=sha-<commit> docker compose -p backend-mcp --env-file .env.prod \
#     -f docker/compose.yml -f docker/compose.prod.yml up -d --no-build --wait
# Images come from GHCR; nothing is built on the VM.

```

In the `migrate:` service, add as its first key:

```yaml
    image: ghcr.io/solvro/backend-mcp-auth:${RELEASE_TAG:?set by the deploy agent}
```

In `auth-service:`, add as its first keys:

```yaml
    image: ghcr.io/solvro/backend-mcp-auth:${RELEASE_TAG:?set by the deploy agent}
    volumes: !reset []   # the base file mounts dev keys from docker/.e2e-keys
```

and change its `FORWARDED_ALLOW_IPS: 10.89.0.10` line to:

```yaml
      # nginx (/health) and the frontend BFF (every API call) - see gateway/nginx/templates.proxy.
      FORWARDED_ALLOW_IPS: "10.89.0.10,10.89.0.11"
```

In `chat-service:`, add the same three things with the chat image:

```yaml
    image: ghcr.io/solvro/backend-mcp-chat:${RELEASE_TAG:?set by the deploy agent}
    volumes: !reset []   # the base file mounts dev keys from docker/.e2e-keys
```

```yaml
      # nginx (/health) and the frontend BFF (every API call) - see gateway/nginx/templates.proxy.
      FORWARDED_ALLOW_IPS: "10.89.0.10,10.89.0.11"
```

At the end of `docker/compose.prod.yml` (after the top-level `secrets:` block), add:

```yaml

networks:
  backend:
    ipam: !override
      config:
        - subnet: 10.89.0.0/24
          # Dynamic addresses come from the upper half only, so the fixed ones below it
          # (nginx 10.89.0.10, frontend 10.89.0.11) are never handed to another container.
          ip_range: 10.89.0.128/25
```

In `scripts/check_prod_secrets.py`, in `rendered_config()`'s `env` dict, add the line:

```python
        "RELEASE_TAG": "sha-check",
```

In `justfile`, replace the `up-prod` and `down-prod` recipes (and their comment) with:

```just
# Production by hand (on the VM the deploy agent does this; see docs/deploy.md). `sha` is a full
# commit sha whose images are on GHCR. Project `backend-mcp`, the same one the agent uses.
up-prod sha: network
    RELEASE_TAG=sha-{{sha}} docker compose -p backend-mcp --env-file .env.prod -f docker/compose.yml -f docker/compose.prod.yml pull
    RELEASE_TAG=sha-{{sha}} docker compose -p backend-mcp --env-file .env.prod -f docker/compose.yml -f docker/compose.prod.yml up -d --no-build --wait

down-prod:
    RELEASE_TAG=unused docker compose -p backend-mcp --env-file .env.prod -f docker/compose.yml -f docker/compose.prod.yml down
```

- [ ] **Step 4: Run the tests and the guard scripts**

Run: `uv run pytest tests/deploy/test_deploy_prod_compose.py -v --no-cov && uv run python scripts/check_prod_secrets.py`
Expected: 5 passed; `production stack: 7 services, no plaintext credentials, all policies present`.

- [ ] **Step 5: Commit**

```bash
git add docker/compose.prod.yml scripts/check_prod_secrets.py justfile tests/deploy/test_deploy_prod_compose.py
git commit -m "feat(deploy): production compose pulls GHCR images under project backend-mcp" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A7: Production edge behind Coolify — plain :80, frontend-only, real client IP

Spec §8 and Review Focus 3.

**Files:**
- Create: `gateway/nginx/templates.proxy/default.conf.template`, `gateway/nginx/templates.proxy/proxy_common.inc.template`, `gateway/nginx/templates.proxy/cors_origins.map.template`
- Modify: `gateway/nginx/nginx.conf` (log format), `docker/compose.prod.yml` (nginx service, top-level secrets), `.env.prod.example`, `pyproject.toml` (register `integration` marker if absent)
- Test: `tests/deploy/test_deploy_prod_compose.py` (add one test), `tests/deploy/test_deploy_nginx_edge.py`

**Interfaces:**
- Consumes: upstream names `auth_service`/`chat_service` from `nginx.conf`; the frontend container's alias `frontend` on port 3000 (Part C).
- Produces: access-log fields `peer=<direct peer> xff="<X-Forwarded-For>"` that `mcpwr-deploy backend proxies` (Task A4) parses; env `TRUSTED_PROXY_CIDR` (default `127.0.0.1/32`).

- [ ] **Step 1: Write the failing compose test**

Append to `tests/deploy/test_deploy_prod_compose.py`:

```python
def test_nginx_is_the_plain_http_edge_behind_coolify(services):
    nginx = services["nginx"]

    assert [(p["published"], p["target"]) for p in nginx["ports"]] == [("80", 80)]
    assert not nginx.get("secrets")
    mounts = {v["target"]: v["source"] for v in nginx["volumes"]}
    assert mounts["/etc/nginx/templates"].endswith("gateway/nginx/templates.proxy")
    assert mounts["/etc/nginx/nginx.conf"].endswith("gateway/nginx/nginx.conf")
    assert nginx["environment"] == {
        "TRUSTED_PROXY_CIDR": "127.0.0.1/32",
        "NGINX_ENVSUBST_FILTER": "^TRUSTED_PROXY_CIDR$",
    }
```

- [ ] **Step 2: Write the failing edge test**

If `pyproject.toml`'s `[tool.pytest.ini_options] markers` has no `integration` entry, add
`"integration: needs Docker or other real backing services",` to the list.

`tests/deploy/test_deploy_nginx_edge.py`:

```python
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
            "inspect", client, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_prod_compose.py tests/deploy/test_deploy_nginx_edge.py -v --no-cov`
Expected: the compose test FAILS (ports `80`+`443`, TLS secrets mounted); the edge fixture FAILS on
`docker run` because `gateway/nginx/templates.proxy` does not exist (or skips if Docker is not running — start Docker; these tests must actually run before this task is done).

- [ ] **Step 4: Write the proxy template set**

`gateway/nginx/templates.proxy/default.conf.template`:

```nginx
# Production edge behind Solvro's Coolify proxy, which terminates TLS for mcpwr.solvro.pl.
# Rendered by envsubst at container start; only ${TRUSTED_PROXY_CIDR} is substituted
# (NGINX_ENVSUBST_FILTER in docker/compose.prod.yml), every other $var is nginx's.
# Public surface: the frontend (/, /bff/) and /health. The backend APIs are reachable only from
# the frontend's BFF over the internal network.

# Docker's embedded DNS. The frontend is another compose project that may be absent or
# mid-deploy, so it is resolved per request instead of once at startup.
resolver 127.0.0.11 valid=10s ipv6=off;

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Only Coolify's proxy may name the client. The default (127.0.0.1/32) trusts no one; set
    # TRUSTED_PROXY_CIDR from `mcpwr-deploy backend proxies` once Coolify is connected.
    set_real_ip_from  ${TRUSTED_PROXY_CIDR};
    real_ip_header    X-Forwarded-For;
    real_ip_recursive on;

    # Every add_header for this vhost lives here: nginx does not inherit add_header into a
    # location that sets its own. HSTS is honoured because browsers get it over Coolify's HTTPS.
    add_header X-Frame-Options           "DENY"                                 always;
    add_header X-Content-Type-Options    "nosniff"                              always;
    add_header Referrer-Policy           "strict-origin-when-cross-origin"      always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains"  always;

    set $frontend http://frontend:3000;

    # The BFF: a chat answer can take chat-service's full 180 s budget.
    location /bff/ {
        include /etc/nginx/conf.d/proxy_common.inc;
        proxy_read_timeout 185s;
        proxy_buffering    off;
        proxy_pass $frontend;
    }

    location = /health {
        include /etc/nginx/conf.d/proxy_common.inc;
        proxy_read_timeout 10s;
        proxy_pass http://chat_service;
    }
    location = /health/live {
        include /etc/nginx/conf.d/proxy_common.inc;
        proxy_read_timeout 5s;
        proxy_pass http://chat_service;
    }

    # Pages, including the email-link targets /auth/verify and /auth/reset-password. Anything
    # else (/api/*, /auth/login, /.well-known/jwks.json, /metrics) is simply a frontend 404.
    location / {
        include /etc/nginx/conf.d/proxy_common.inc;
        proxy_read_timeout 30s;
        proxy_pass $frontend;
    }
}
```

`gateway/nginx/templates.proxy/proxy_common.inc.template`:

```nginx
# Shared upstream settings for the production edge, included per location.
proxy_http_version 1.1;
proxy_set_header Connection        "";
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
# $remote_addr is the client once real_ip has run. Overwrite rather than append, so no
# client-supplied chain ever reaches the frontend or the services.
proxy_set_header X-Forwarded-For   $remote_addr;
# The public entry point is always HTTPS, terminated at Coolify.
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Request-ID      $request_id_final;

proxy_connect_timeout 5s;
proxy_send_timeout    30s;
# proxy_read_timeout is per-location: it is the latency budget of each upstream.

# The edge owns these; a second copy from the app would duplicate them.
proxy_hide_header X-Frame-Options;
proxy_hide_header X-Content-Type-Options;
proxy_hide_header Referrer-Policy;
proxy_hide_header Strict-Transport-Security;
```

`gateway/nginx/templates.proxy/cors_origins.map.template`:

```nginx
# Production serves the frontend same-origin and exposes no API to browsers, so no origin is
# allow-listed. nginx.conf includes this file inside its $cors_origin map; it must exist.
```

In `gateway/nginx/nginx.conf`, replace the `log_format edge` statement with:

```nginx
    # peer = the direct TCP peer (Coolify's proxy in production), xff = what it forwarded;
    # `mcpwr-deploy backend proxies` reads these to find the address to trust.
    log_format edge '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" "$http_user_agent" '
                    'rid=$request_id_final rt=$request_time urt=$upstream_response_time '
                    'peer=$realip_remote_addr xff="$http_x_forwarded_for"';
```

- [ ] **Step 5: Point the production overlay at it**

In `docker/compose.prod.yml`, replace the whole `nginx:` service with:

```yaml
  nginx:
    restart: *restart
    logging: *logging
    read_only: true
    tmpfs: [/var/cache/nginx, /var/run, /etc/nginx/conf.d]   # envsubst writes rendered confs here
    # Behind Coolify, which terminates TLS for mcpwr.solvro.pl: plain HTTP on :80, no certs.
    volumes: !override
      - ../gateway/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ../gateway/nginx/templates.proxy:/etc/nginx/templates:ro
    secrets: !reset []
    ports: !override
      - "80:80"
    environment: !override
      # Coolify's proxy address(es), the only peers allowed to set X-Forwarded-For.
      TRUSTED_PROXY_CIDR: ${TRUSTED_PROXY_CIDR:-127.0.0.1/32}
      NGINX_ENVSUBST_FILTER: ^TRUSTED_PROXY_CIDR$$
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 128m }
```

and delete the `tls_cert:` and `tls_key:` lines from its top-level `secrets:` block.

In `.env.prod.example`, replace the `# Edge` block (`SERVER_NAME=`, `CORS_ALLOWED_ORIGIN=`) with:

```bash
# Edge (behind Coolify, which terminates TLS for mcpwr.solvro.pl)
# Address(es) allowed to set X-Forwarded-For. 127.0.0.1/32 trusts no one; once Coolify is
# connected, set it from `mcpwr-deploy backend proxies` (docs/deploy.md).
TRUSTED_PROXY_CIDR=127.0.0.1/32
```

and change `FRONTEND_URL=https://app.example.com` to `FRONTEND_URL=https://mcpwr.solvro.pl`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/test_deploy_prod_compose.py tests/deploy/test_deploy_nginx_edge.py -v --no-cov && uv run python scripts/check_prod_secrets.py`
Expected: 6 + 15 passed; the checker prints `production stack: 7 services, …`.

- [ ] **Step 7: Confirm the dev edge still works**

Run: `just up && curl -sk https://localhost:8443/health/live && just down`
Expected: `{"status":"ok"…}` — the shared `nginx.conf` change (log format) must not break the TLS edge.

- [ ] **Step 8: Commit**

```bash
git add gateway/nginx docker/compose.prod.yml .env.prod.example pyproject.toml tests/deploy/test_deploy_prod_compose.py tests/deploy/test_deploy_nginx_edge.py
git commit -m "feat(gateway): production edge behind coolify with real client ip" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A8: Publish images to GHCR on `main`

Spec §6.

**Files:**
- Modify: `.github/workflows/main.yaml` (new `publish` job)

**Interfaces:**
- Consumes: job ids `test`, `build-check`, `migrations` in `main.yaml`.
- Produces: `ghcr.io/solvro/backend-mcp-auth` and `ghcr.io/solvro/backend-mcp-chat` tagged `sha-<sha>` then `main` (Task A1's agent watches `backend-mcp-auth:main`).

- [ ] **Step 1: Add the job**

Append to `jobs:` in `.github/workflows/main.yaml`:

```yaml
  publish:
    name: Publish images
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    needs: [test, build-check, migrations]
    permissions:
      contents: read
      packages: write
    # Queue publishes instead of running them side by side: an older run finishing last would
    # move `main` back to an older commit.
    concurrency:
      group: publish-main
      cancel-in-progress: false
    env:
      REGISTRY: ghcr.io/solvro

    steps:
      - name: Checkout
        uses: actions/checkout@v6

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push auth-service
        uses: docker/build-push-action@v6
        with:
          context: .
          file: services/auth-service/Dockerfile
          platforms: linux/amd64
          push: true
          tags: ${{ env.REGISTRY }}/backend-mcp-auth:sha-${{ github.sha }}
          labels: |
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
          cache-from: type=gha,scope=backend-mcp-auth
          cache-to: type=gha,mode=max,scope=backend-mcp-auth

      - name: Build and push chat-service
        uses: docker/build-push-action@v6
        with:
          context: .
          file: services/chat-service/Dockerfile
          platforms: linux/amd64
          push: true
          tags: ${{ env.REGISTRY }}/backend-mcp-chat:sha-${{ github.sha }}
          labels: |
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
          cache-from: type=gha,scope=backend-mcp-chat
          cache-to: type=gha,mode=max,scope=backend-mcp-chat

      - name: Check the images carry no keys or env files
        run: |
          for image in backend-mcp-auth backend-mcp-chat; do
            ref="$REGISTRY/$image:sha-$GITHUB_SHA"
            docker pull -q "$ref" >/dev/null
            found=$(docker run --rm --entrypoint sh "$ref" -c \
              'find /app -path /app/.venv -prune -o \( -name "*.pem" -o -name "*.key" -o -name ".env" -o -name ".env.*" \) -print')
            if [ -n "$found" ]; then
              echo "::error::$image contains key or env files:"
              echo "$found"
              exit 1
            fi
          done

      # Last, so the deploy agent never sees a `main` whose sibling image is missing.
      - name: Move the main tags
        run: |
          for image in backend-mcp-auth backend-mcp-chat; do
            docker buildx imagetools create -t "$REGISTRY/$image:main" "$REGISTRY/$image:sha-$GITHUB_SHA"
          done
```

- [ ] **Step 2: Lint the workflow**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest .github/workflows/main.yaml`
Expected: no findings for the `publish` job (findings in pre-existing jobs are out of scope; note them in the PR).

- [ ] **Step 3: Check the image content locally the same way**

Run:
```bash
just build
for image in ml-mcp-backend/auth-service:dev ml-mcp-backend/chat-service:dev; do
  docker run --rm --entrypoint sh "$image" -c 'find /app -path /app/.venv -prune -o \( -name "*.pem" -o -name "*.key" -o -name ".env" -o -name ".env.*" \) -print'
done
```
Expected: no output. (`just build` tags chat-service as `ml-mcp-backend/chat-service:dev`.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/main.yaml
git commit -m "ci(deploy): publish backend images to ghcr on main" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A9: `init-secrets.sh` — generate once, never overwrite, never print

Spec §9 and Review Focus 4.

**Files:**
- Create: `deploy/vm/init-secrets.sh`
- Test: `tests/deploy/test_deploy_init_secrets.py`

**Interfaces:**
- Consumes: `.env.prod.example` (Task A7 version: `POSTGRES_USER`, `POSTGRES_DB`, `MONGO_ROOT_USER`), ml-mcp's `.env.prod.example` (Part B) from `ML_MCP_ENV_EXAMPLE_URL`.
- Produces: the 13 files `docker/compose.prod.yml` mounts (`postgres_password mongo_root_password redis_password database_url mongo_uri redis_url jwt_private_key.pem jwt_public_key.pem jwt_previous_public_key.pem openai_api_key google_api_key langfuse_secret_key smtp_pass`), `/opt/mcpwr/stacks/backend/.env.prod`, `/opt/mcpwr/stacks/ml-mcp/.env`.
- Environment overrides (tests only): `SECRETS_DIR`, `MCPWR_HOME`, `SRC_DIR`, `ML_MCP_ENV_EXAMPLE_URL`, `MCPWR_SKIP_CHOWN=1`.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_init_secrets.py`:

```python
import os
import re
import stat
import subprocess
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

SCRIPT = ROOT / "deploy/vm/init-secrets.sh"
GENERATED = ["postgres_password", "mongo_root_password", "redis_password"]
DERIVED = ["database_url", "mongo_uri", "redis_url"]
KEYS = ["jwt_private_key.pem", "jwt_public_key.pem"]
EMPTY = ["jwt_previous_public_key.pem", "openai_api_key", "google_api_key", "langfuse_secret_key", "smtp_pass"]
ML_EXAMPLE = "NEO4J_URI=bolt://neo4j:7687\nNEO4J_USER=neo4j\nNEO4J_PASSWORD=\nOPENAI_API_KEY=\n"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture
def layout(tmp_path):
    example = tmp_path / "ml-mcp.env.prod.example"
    example.write_text(ML_EXAMPLE)
    home = tmp_path / "opt"
    env = {
        **os.environ,
        "SECRETS_DIR": str(tmp_path / "secrets"),
        "MCPWR_HOME": str(home),
        "SRC_DIR": str(ROOT),
        "ML_MCP_ENV_EXAMPLE_URL": example.as_uri(),
        "MCPWR_SKIP_CHOWN": "1",
    }

    def run(**extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, timeout=60
        )

    return SimpleNamespace(
        run=run,
        secrets=tmp_path / "secrets",
        backend_env=home / "stacks/backend/.env.prod",
        ml_env=home / "stacks/ml-mcp/.env",
    )


def mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def env_value(path, key: str) -> str:
    return next(line.split("=", 1)[1] for line in path.read_text().splitlines() if line.startswith(f"{key}="))


def test_creates_every_secret_with_tight_permissions(layout):
    result = layout.run()

    assert result.returncode == 0, result.stderr
    assert mode(layout.secrets) == 0o750
    for name in GENERATED + DERIVED + KEYS + EMPTY:
        assert mode(layout.secrets / name) == 0o440, name
    for name in GENERATED:
        assert HEX64.match((layout.secrets / name).read_text()), name
    for name in EMPTY:
        assert (layout.secrets / name).read_text() == "", name


def test_connection_urls_use_the_generated_passwords(layout):
    assert layout.run().returncode == 0

    pg, mongo, redis = ((layout.secrets / n).read_text() for n in GENERATED)
    user, db = env_value(layout.backend_env, "POSTGRES_USER"), env_value(layout.backend_env, "POSTGRES_DB")
    mongo_user = env_value(layout.backend_env, "MONGO_ROOT_USER")
    assert (layout.secrets / "database_url").read_text() == f"postgresql+asyncpg://{user}:{pg}@postgres:5432/{db}"
    assert (layout.secrets / "mongo_uri").read_text() == f"mongodb://{mongo_user}:{mongo}@mongo:27017/?authSource=admin"
    assert (layout.secrets / "redis_url").read_text() == f"redis://:{redis}@redis:6379"


def test_jwt_keypair_matches(layout):
    assert layout.run().returncode == 0

    private = serialization.load_pem_private_key((layout.secrets / "jwt_private_key.pem").read_bytes(), None)
    public = serialization.load_pem_public_key((layout.secrets / "jwt_public_key.pem").read_bytes())
    assert private.public_key().public_numbers() == public.public_numbers()


def test_env_files_come_from_their_examples(layout):
    assert layout.run().returncode == 0

    assert layout.backend_env.read_text() == (ROOT / ".env.prod.example").read_text()
    assert mode(layout.backend_env) == 0o640
    assert mode(layout.ml_env) == 0o640
    assert HEX64.match(env_value(layout.ml_env, "NEO4J_PASSWORD"))
    assert env_value(layout.ml_env, "NEO4J_URI") == "bolt://neo4j:7687"


def test_output_names_files_but_never_contents(layout):
    result = layout.run()

    output = result.stdout + result.stderr
    for name in GENERATED:
        assert (layout.secrets / name).read_text() not in output
    assert env_value(layout.ml_env, "NEO4J_PASSWORD") not in output
    assert "PRIVATE KEY" not in output


def test_rerun_changes_nothing(layout):
    assert layout.run().returncode == 0
    files = [p for p in [*layout.secrets.iterdir(), layout.backend_env, layout.ml_env]]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    result = layout.run()

    assert result.returncode == 0, result.stderr
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files} == before
    assert "created" not in result.stdout


def test_existing_neo4j_password_is_kept(layout):
    layout.ml_env.parent.mkdir(parents=True)
    layout.ml_env.write_text("NEO4J_PASSWORD=chosen-by-a-human\n")

    assert layout.run().returncode == 0

    assert env_value(layout.ml_env, "NEO4J_PASSWORD") == "chosen-by-a-human"


def test_failed_example_download_leaves_no_half_written_file(layout, tmp_path):
    result = layout.run(ML_MCP_ENV_EXAMPLE_URL=(tmp_path / "missing").as_uri())

    assert result.returncode != 0
    assert not layout.ml_env.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_init_secrets.py -v --no-cov`
Expected: all 8 FAIL — `bash: .../deploy/vm/init-secrets.sh: No such file or directory`.

- [ ] **Step 3: Write the script**

`deploy/vm/init-secrets.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# One-time creation of everything production needs that must not live in git (spec section 9):
# the backend's docker-secret files and both stacks' env files. Never overwrites an existing
# file, so re-running it is safe; prints file names only, never contents.
#
#   sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh
set -euo pipefail

SECRETS_DIR="${SECRETS_DIR:-/etc/ml-mcp/secrets}"
MCPWR_HOME="${MCPWR_HOME:-/opt/mcpwr}"
SRC_DIR="${SRC_DIR:-$MCPWR_HOME/src/backend-mcp}"
ML_MCP_ENV_EXAMPLE_URL="${ML_MCP_ENV_EXAMPLE_URL:-https://raw.githubusercontent.com/Solvro/ml-mcp/main/.env.prod.example}"
DEPLOY_GROUP="${DEPLOY_GROUP:-mcpwr-deploy}"
BACKEND_ENV="$MCPWR_HOME/stacks/backend/.env.prod"
ML_MCP_ENV="$MCPWR_HOME/stacks/ml-mcp/.env"

umask 077

own() { # <mode> <path>
  chmod "$1" "$2"
  if [ "${MCPWR_SKIP_CHOWN:-0}" != 1 ]; then chown "root:$DEPLOY_GROUP" "$2"; fi
}

write_new() { # <path> <mode>: stdin becomes the file, unless the file already exists
  if [ -e "$1" ]; then
    cat >/dev/null
    printf 'kept    %s\n' "$1"
    return 0
  fi
  cat >"$1"
  own "$2" "$1"
  printf 'created %s\n' "$1"
}

env_value() { # <file> <KEY>
  sed -n "s/^$2=//p" "$1" | tail -n 1
}

random_secret() { openssl rand -hex 32 | tr -d '\n'; }

mkdir -p "$SECRETS_DIR" "$(dirname "$BACKEND_ENV")" "$(dirname "$ML_MCP_ENV")"
own 0750 "$SECRETS_DIR"

# 1. Env files from their examples. Humans fill in FRONTEND_URL, SMTP_* and the LLM keys.
write_new "$BACKEND_ENV" 0640 <"$SRC_DIR/.env.prod.example"
if [ -e "$ML_MCP_ENV" ]; then
  printf 'kept    %s\n' "$ML_MCP_ENV"
else
  example=$(mktemp)
  if ! curl -fsS "$ML_MCP_ENV_EXAMPLE_URL" -o "$example"; then
    rm -f "$example"
    printf 'could not download %s\n' "$ML_MCP_ENV_EXAMPLE_URL" >&2
    exit 1
  fi
  write_new "$ML_MCP_ENV" 0640 <"$example"
  rm -f "$example"
fi
if [ -z "$(env_value "$ML_MCP_ENV" NEO4J_PASSWORD)" ]; then
  awk -v pw="$(random_secret)" '
    /^NEO4J_PASSWORD=/ { print "NEO4J_PASSWORD=" pw; done = 1; next }
    { print }
    END { if (!done) print "NEO4J_PASSWORD=" pw }
  ' "$ML_MCP_ENV" >"$ML_MCP_ENV.new"
  cat "$ML_MCP_ENV.new" >"$ML_MCP_ENV" # keeps the file's mode and owner
  rm -f "$ML_MCP_ENV.new"
  printf 'set     NEO4J_PASSWORD in %s\n' "$ML_MCP_ENV"
fi

# 2. Database passwords, and the connection URLs built from them.
for name in postgres_password mongo_root_password redis_password; do
  random_secret | write_new "$SECRETS_DIR/$name" 0440
done
pg_user=$(env_value "$BACKEND_ENV" POSTGRES_USER)
pg_db=$(env_value "$BACKEND_ENV" POSTGRES_DB)
mongo_user=$(env_value "$BACKEND_ENV" MONGO_ROOT_USER)
if [ -z "$pg_user" ] || [ -z "$pg_db" ] || [ -z "$mongo_user" ]; then
  printf 'set POSTGRES_USER, POSTGRES_DB and MONGO_ROOT_USER in %s first\n' "$BACKEND_ENV" >&2
  exit 1
fi
printf 'postgresql+asyncpg://%s:%s@postgres:5432/%s' "$pg_user" "$(cat "$SECRETS_DIR/postgres_password")" "$pg_db" |
  write_new "$SECRETS_DIR/database_url" 0440
printf 'mongodb://%s:%s@mongo:27017/?authSource=admin' "$mongo_user" "$(cat "$SECRETS_DIR/mongo_root_password")" |
  write_new "$SECRETS_DIR/mongo_uri" 0440
printf 'redis://:%s@redis:6379' "$(cat "$SECRETS_DIR/redis_password")" |
  write_new "$SECRETS_DIR/redis_url" 0440

# 3. The RS256 signing pair; the "previous" public key stays empty until the first rotation.
private="$SECRETS_DIR/jwt_private_key.pem"
public="$SECRETS_DIR/jwt_public_key.pem"
if [ -e "$private" ] && [ -e "$public" ]; then
  printf 'kept    %s\nkept    %s\n' "$private" "$public"
elif [ -e "$private" ] || [ -e "$public" ]; then
  printf 'only one half of the JWT keypair exists in %s; restore or remove it by hand\n' "$SECRETS_DIR" >&2
  exit 1
else
  keys=$(mktemp -d)
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$keys/private.pem" 2>/dev/null
  openssl pkey -in "$keys/private.pem" -pubout -out "$keys/public.pem" 2>/dev/null
  write_new "$private" 0440 <"$keys/private.pem"
  write_new "$public" 0440 <"$keys/public.pem"
  rm -rf "$keys"
fi
: | write_new "$SECRETS_DIR/jwt_previous_public_key.pem" 0440

# 4. External credentials: created empty, filled by a human with sudoedit.
for name in openai_api_key google_api_key langfuse_secret_key smtp_pass; do
  : | write_new "$SECRETS_DIR/$name" 0440
done

cat <<EOF

Fill in by hand with sudoedit (never paste the values anywhere else):
  $SECRETS_DIR/{openai_api_key,google_api_key,langfuse_secret_key,smtp_pass}  (empty = unused)
  $BACKEND_ENV   FRONTEND_URL, SMTP_HOST, SMTP_USER, SMTP_FROM
  $ML_MCP_ENV    OPENAI_/DEEPSEEK_/GOOGLE_/CLARIN_API_KEY, LANGFUSE_*
EOF
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/test_deploy_init_secrets.py -v --no-cov && just lint-sh`
Expected: 8 passed; shellcheck clean.

- [ ] **Step 5: Commit**

```bash
git add deploy/vm/init-secrets.sh tests/deploy/test_deploy_init_secrets.py
git update-index --chmod=+x deploy/vm/init-secrets.sh
git commit -m "feat(deploy): init-secrets script that never overwrites or prints secrets" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A10: Nightly backups of Postgres and Mongo

Spec §10.

**Files:**
- Create: `deploy/vm/backup.sh`, `deploy/systemd/mcpwr-backup.service`, `deploy/systemd/mcpwr-backup.timer`
- Test: `tests/deploy/test_deploy_backup.py`

**Interfaces:**
- Consumes: the `sandbox` fixture (Task A1); container env `POSTGRES_USER`, `POSTGRES_DB`, `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD_FILE` set by `docker/compose.prod.yml`.
- Produces: `/var/backups/mcpwr/<YYYY-MM-DD>/{postgres.dump,mongo.archive.gz}` (0600); installed as `/opt/mcpwr/agent/backup.sh` by Task A11. Env overrides: `BACKUP_DIR`, `KEEP_DAYS`, `PROJECT`.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_backup.py`:

```python
import os
import stat
import time
from datetime import UTC, datetime

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

SCRIPT = ROOT / "deploy/vm/backup.sh"
POSTGRES = [
    {"cmd": "docker", "match": r"label=com\.docker\.compose\.service=postgres$", "stdout": "pg1\n"},
    {"cmd": "docker", "match": r"^exec pg1 ", "stdout": "PGDUMP"},
]
MONGO = [
    {"cmd": "docker", "match": r"label=com\.docker\.compose\.service=mongo$", "stdout": "mg1\n"},
    {"cmd": "docker", "match": r"^exec mg1 ", "stdout": "MONGOARCHIVE"},
]


def run_backup(sandbox, backups):
    return sandbox.run(SCRIPT, BACKUP_DIR=str(backups))


def today(backups):
    return backups / datetime.now(UTC).strftime("%Y-%m-%d")


def test_writes_both_dumps_readable_only_by_root(sandbox, tmp_path):
    sandbox.rules = POSTGRES + MONGO
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 0, result.stderr
    day = today(backups)
    assert (day / "postgres.dump").read_text() == "PGDUMP"
    assert (day / "mongo.archive.gz").read_text() == "MONGOARCHIVE"
    for name in ("postgres.dump", "mongo.archive.gz"):
        assert stat.S_IMODE((day / name).stat().st_mode) == 0o600
    execs = [c["args"] for c in sandbox.calls("docker") if c["args"][0] == "exec"]
    assert "pg_dump" in execs[0][-1] and "-Fc" in execs[0][-1]
    assert "mongodump" in execs[1][-1] and "--archive" in execs[1][-1]


def test_old_backups_are_removed_and_recent_ones_kept(sandbox, tmp_path):
    sandbox.rules = POSTGRES + MONGO
    backups = tmp_path / "backups"
    old, recent = backups / "2026-01-01", backups / "2026-01-20"
    for path, days in ((old, 10), (recent, 3)):
        path.mkdir(parents=True)
        then = time.time() - days * 86400
        os.utime(path, (then, then))

    assert run_backup(sandbox, backups).returncode == 0

    assert not old.exists()
    assert recent.exists()


def test_missing_container_fails_but_the_other_dump_is_written(sandbox, tmp_path):
    sandbox.rules = POSTGRES  # mongo is not running
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 1
    assert "mongo is not running" in result.stderr
    assert (today(backups) / "postgres.dump").exists()
    assert sorted(p.name for p in today(backups).iterdir()) == ["postgres.dump"]


def test_failed_dump_leaves_no_partial_file(sandbox, tmp_path):
    sandbox.rules = [
        {"cmd": "docker", "match": r"^exec pg1 ", "exit": 1},
        *POSTGRES,
        *MONGO,
    ]
    backups = tmp_path / "backups"

    result = run_backup(sandbox, backups)

    assert result.returncode == 1
    assert sorted(p.name for p in today(backups).iterdir()) == ["mongo.archive.gz"]


def test_timer_runs_the_installed_script_nightly():
    service = (ROOT / "deploy/systemd/mcpwr-backup.service").read_text()
    timer = (ROOT / "deploy/systemd/mcpwr-backup.timer").read_text()

    assert "ExecStart=/opt/mcpwr/agent/backup.sh" in service
    assert "OnCalendar=*-*-* 03:00:00" in timer
    assert "Persistent=true" in timer
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_backup.py -v --no-cov`
Expected: all 5 FAIL (script and units missing).

- [ ] **Step 3: Write the script and units**

`deploy/vm/backup.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# Nightly Postgres + Mongo dumps of the backend-mcp stack (spec section 10). Run as root by
# mcpwr-backup.service. Redis is ephemeral and Neo4j is rebuilt from its dump, so neither is here.
# Restore commands: docs/deploy.md.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/mcpwr}"
KEEP_DAYS="${KEEP_DAYS:-7}"
PROJECT="${PROJECT:-backend-mcp}"

umask 077

container() { # <compose service> -> container id, or nothing
  docker ps -q \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1"
}

dump() { # <service> <file name> <command run inside the container>
  local cid out="$DAY/$2"
  cid=$(container "$1")
  if [ -z "$cid" ]; then
    printf '<3>%s is not running; no %s tonight\n' "$1" "$2" >&2
    return 1
  fi
  if docker exec "$cid" sh -c "$3" >"$out.partial"; then
    mv "$out.partial" "$out"
    printf '<6>wrote %s\n' "$out" >&2
  else
    rm -f "$out.partial"
    printf '<3>%s dump failed\n' "$1" >&2
    return 1
  fi
}

DAY="$BACKUP_DIR/$(date -u +%Y-%m-%d)"
mkdir -p "$DAY"
failed=0
# shellcheck disable=SC2016 # expanded inside the container, not here
dump postgres postgres.dump 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' || failed=1
# shellcheck disable=SC2016 # expanded inside the container, not here
dump mongo mongo.archive.gz \
  'mongodump --quiet --archive --gzip --authenticationDatabase admin -u "$MONGO_INITDB_ROOT_USERNAME" -p "$(cat "$MONGO_INITDB_ROOT_PASSWORD_FILE")"' ||
  failed=1
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +
exit "$failed"
```

`deploy/systemd/mcpwr-backup.service`:

```ini
[Unit]
Description=Nightly Postgres and Mongo backups of the backend-mcp stack
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/opt/mcpwr/agent/backup.sh
```

`deploy/systemd/mcpwr-backup.timer`:

```ini
[Unit]
Description=Nightly mcpwr backups

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/deploy/test_deploy_backup.py -v --no-cov && just lint-sh`
Expected: 5 passed; shellcheck clean.

- [ ] **Step 5: Commit**

```bash
git add deploy/vm/backup.sh deploy/systemd/mcpwr-backup.service deploy/systemd/mcpwr-backup.timer tests/deploy/test_deploy_backup.py
git update-index --chmod=+x deploy/vm/backup.sh
git commit -m "feat(deploy): nightly postgres and mongo backups with a week of retention" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A11: `bootstrap.sh` and the production runbook

Spec §5, §9 (runbook), §10.

**Files:**
- Create: `deploy/vm/bootstrap.sh`
- Modify (rewrite): `docs/deploy.md`
- Test: `tests/deploy/test_deploy_bootstrap.py`

**Interfaces:**
- Consumes: every file under `deploy/` (Tasks A1–A10) — installs them to the paths in Global Constraints.
- Produces: `bootstrap.sh [--ref <ref>] [--enable-timers]`; exit 2 on bad arguments, 1 when not root / unsupported OS.

- [ ] **Step 1: Write the failing tests**

`tests/deploy/test_deploy_bootstrap.py` (the provisioning itself is verified on the VM in Part D;
these pin the argument handling that runs before anything is touched):

```python
import os
import subprocess

import pytest
from deploy_harness import ROOT

pytestmark = pytest.mark.unit

SCRIPT = ROOT / "deploy/vm/bootstrap.sh"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30)


def test_help_explains_the_options():
    result = run("--help")

    assert result.returncode == 0
    assert "--ref" in result.stderr and "--enable-timers" in result.stderr


def test_unknown_arguments_are_rejected_before_anything_runs():
    result = run("--nope")

    assert result.returncode == 2
    assert "usage:" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="must run as a normal user")
def test_refuses_to_run_without_root():
    result = run("--ref", "main")

    assert result.returncode == 1
    assert "run as root" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/deploy/test_deploy_bootstrap.py -v --no-cov`
Expected: all 3 FAIL (script missing).

- [ ] **Step 3: Write the script**

`deploy/vm/bootstrap.sh` (then `chmod +x`):

```bash
#!/usr/bin/env bash
# Prepares the mcpwr VM for production (spec section 5). Idempotent: re-run it after any change
# under deploy/ - the deploy agent does not update itself.
#
#   first run:  ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh
#   later:      sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main [--enable-timers]
set -euo pipefail

# When run from the checkout it updates, re-exec from a copy first: bash reads scripts
# incrementally, and the `git checkout` below may rewrite this very file mid-run.
if [ -z "${MCPWR_BOOTSTRAP_COPY:-}" ] && [ -f "$0" ]; then
  copy=$(mktemp)
  cp "$0" "$copy"
  MCPWR_BOOTSTRAP_COPY="$copy" exec bash "$copy" "$@"
fi
if [ -n "${MCPWR_BOOTSTRAP_COPY:-}" ]; then trap 'rm -f "$MCPWR_BOOTSTRAP_COPY"' EXIT; fi

REPO="https://github.com/Solvro/backend-mcp.git"
HOME_DIR=/opt/mcpwr
SRC="$HOME_DIR/src/backend-mcp"
REF=main
ENABLE_TIMERS=0

usage() {
  cat >&2 <<'EOF'
usage: bootstrap.sh [--ref <branch|tag|sha>] [--enable-timers]
  --ref            what to install the agent, configs and units from (default: main)
  --enable-timers  also start the per-stack deploy timers and the nightly backup timer
EOF
  exit "${1:-2}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      [ $# -ge 2 ] || usage
      REF="$2"
      shift 2
      ;;
    --enable-timers)
      ENABLE_TIMERS=1
      shift
      ;;
    -h | --help) usage 0 ;;
    *) usage ;;
  esac
done

log() { printf '==> %s\n' "$*"; }
die() {
  printf 'bootstrap: %s\n' "$*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in ubuntu | debian) ;; *) die "unsupported OS: $ID (Debian/Ubuntu only)" ;; esac
export DEBIAN_FRONTEND=noninteractive

log "packages"
apt-get update -q
apt-get install -y -q ca-certificates curl git jq openssl ufw unattended-upgrades util-linux

if ! command -v docker >/dev/null 2>&1; then
  log "docker engine from Docker's apt repository"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$VERSION_CODENAME" >/etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

log "docker daemon settings"
desired='{"live-restore": true, "log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "5"}}'
mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ] ||
  [ "$(jq -S . /etc/docker/daemon.json)" != "$(printf '%s' "$desired" | jq -S .)" ]; then
  printf '%s' "$desired" | jq . >/etc/docker/daemon.json
  # Until live-restore is on, this restart also restarts running containers (unless-stopped).
  systemctl restart docker
fi
systemctl enable --now docker >/dev/null

log "unattended security upgrades that never reboot on their own"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat >/etc/apt/apt.conf.d/52mcpwr-no-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF

log "deploy user"
if ! id mcpwr-deploy >/dev/null 2>&1; then
  useradd --system --home-dir "$HOME_DIR" --no-create-home --shell /usr/sbin/nologin mcpwr-deploy
fi
usermod -aG docker mcpwr-deploy

log "directories"
install -d -o root -g root -m 0755 "$HOME_DIR" "$HOME_DIR/agent" "$HOME_DIR/agent/stacks" "$HOME_DIR/src"
install -d -o mcpwr-deploy -g mcpwr-deploy -m 0755 "$HOME_DIR/stacks" "$HOME_DIR/state" \
  "$HOME_DIR/stacks/backend" "$HOME_DIR/stacks/ml-mcp" "$HOME_DIR/stacks/frontend"
install -d -o mcpwr-deploy -g mcpwr-deploy -m 0700 "$HOME_DIR/.docker" # the docker CLI's config dir
install -d -o root -g mcpwr-deploy -m 0750 /etc/ml-mcp /etc/ml-mcp/secrets
install -d -o root -g root -m 0700 /var/backups/mcpwr

log "backend-mcp checkout at $REF"
if [ ! -d "$SRC/.git" ]; then git clone -q "$REPO" "$SRC"; fi
git -C "$SRC" fetch -q origin "$REF"
git -C "$SRC" checkout -q --detach FETCH_HEAD

log "agent, stack configs and systemd units"
install -o root -g root -m 0755 "$SRC/deploy/agent/mcpwr-deploy" "$HOME_DIR/agent/mcpwr-deploy"
install -o root -g root -m 0755 "$SRC/deploy/vm/backup.sh" "$HOME_DIR/agent/backup.sh"
install -o root -g root -m 0644 "$SRC"/deploy/agent/stacks/*.conf "$HOME_DIR/agent/stacks/"
install -o root -g root -m 0644 "$SRC"/deploy/systemd/* /etc/systemd/system/
systemctl daemon-reload

log "shared network solvro-mcp-internal"
docker network inspect solvro-mcp-internal >/dev/null 2>&1 ||
  docker network create --internal solvro-mcp-internal >/dev/null

log "ssh"
admin="${SUDO_USER:-mlai}"
admin_home=$(getent passwd "$admin" | cut -d: -f6 || true)
if [ -n "$admin_home" ] && [ -s "$admin_home/.ssh/authorized_keys" ]; then
  # 10- sorts before cloud-init's 50-cloud-init.conf, and sshd keeps the first value it reads.
  cat >/etc/ssh/sshd_config.d/10-mcpwr.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
  sshd -t
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
else
  log "WARNING: $admin has no ~/.ssh/authorized_keys - leaving SSH password login enabled"
fi

log "firewall"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw --force enable >/dev/null
# Docker-published ports (nginx :80) bypass ufw; limiting :80 to Coolify is spec follow-up F2.

if [ "$ENABLE_TIMERS" = 1 ]; then
  log "timers"
  for stack in ml-mcp backend frontend; do
    systemctl enable --now "mcpwr-deploy@$stack.timer"
  done
  systemctl enable --now mcpwr-backup.timer
fi

log "done"
cat <<EOF
Next:
  sudo $SRC/deploy/vm/init-secrets.sh          first time only; never overwrites
  sudo $SRC/deploy/vm/bootstrap.sh --ref $REF --enable-timers
  sudo -u mcpwr-deploy $HOME_DIR/agent/mcpwr-deploy backend status
EOF
```

- [ ] **Step 4: Run the tests and shellcheck**

Run: `uv run pytest tests/deploy/test_deploy_bootstrap.py -v --no-cov && just lint-sh`
Expected: 3 passed; shellcheck clean.

- [ ] **Step 5: Rewrite `docs/deploy.md`**

Replace the whole file with:

````markdown
# Deploying ml-mcp-backend

| | `just up` (dev) | production (mcpwr VM) |
|---|---|---|
| how | `docker/compose.yml` + `--profile dev` | the deploy agent, from GHCR images (`just up-prod <sha>` by hand) |
| project | `ml-mcp-backend` | `backend-mcp` |
| secrets | throwaway: `docker/.tls/` (self-signed), `docker/.e2e-keys/` (JWT) | files in `/etc/ml-mcp/secrets`, mounted as docker secrets |
| ingress | `:8080` -> 301 -> `https://localhost:8443` | plain `:80` behind Coolify, which terminates TLS for `mcpwr.solvro.pl` |
| extras | Mailpit, host ports on every service | none; only nginx publishes a port |

Design: `docs/superpowers/specs/2026-09-23-vm-continuous-deployment-design.md`.

## Edge (`gateway/nginx/`)

`nginx.conf` holds the upstreams, the CORS maps and the access-log format; two template sets are
rendered by the image's envsubst step.

**Dev, `templates/`** (from `SERVER_NAME`, `CORS_ALLOWED_ORIGIN`, `PUBLIC_HTTPS_SUFFIX`):

- TLS 1.2/1.3 from `/run/secrets/tls_cert` + `tls_key`; HSTS on 443 only.
- `/auth/*` -> auth-service (30s), `/api/*` -> chat-service (180s, unbuffered),
  `/health` -> chat, `/health/auth` -> auth, `/.well-known/jwks.json` -> auth, else 404.
- CORS is decided here and stripped from upstream responses.

**Production, `templates.proxy/`** (from `TRUSTED_PROXY_CIDR` only):

- Plain HTTP on :80, `server_name _`. No TLS, no redirect: Coolify terminates TLS.
- `/bff/` -> frontend (185 s, unbuffered), `/health` and `/health/live` -> chat-service,
  everything else -> frontend. The backend APIs are not public: only the frontend's BFF reaches
  them, over the internal network.
- `set_real_ip_from ${TRUSTED_PROXY_CIDR}`: only Coolify's proxy may name the client. nginx then
  **overwrites** `X-Forwarded-For` with that one address and sends `X-Forwarded-Proto: https`.
  The frontend (fixed IP `10.89.0.11`) forwards it; auth- and chat-service trust
  `10.89.0.10,10.89.0.11` (`FORWARDED_ALLOW_IPS`).
- The access log's `peer=` / `xff=` fields are how you find Coolify's address (below).

Body cap 1 MiB everywhere, mirroring `MAX_REQUEST_BODY_SIZE`.

## Production VM

`mlai@10.21.36.20` (VPN), public name `mcpwr.solvro.pl`. Three stacks — `backend-mcp`, `ml-mcp`,
`frontend-mcp` — each updated by `mcpwr-deploy@<stack>.timer`:

1. CI publishes `ghcr.io/solvro/<image>:sha-<commit>` and moves `:main` last.
2. Every minute the agent compares `:main`'s digest with what it deployed. On a change it checks
   out that commit's compose files, pulls, runs migrations (backend), `up --wait`s, and gates on
   `GET /health` (`ok` or `degraded`).
3. A failed gate rolls back to the previous release (never re-running migrations) and marks the
   digest bad until `main` moves again. A failed migration marks it bad and touches nothing.

### Migrations must keep the previous release working

Automatic rollback runs the previous release against the *current* schema. Add columns/tables
first, stop using them in a later release, drop them in a later one still.

### First bring-up

Prerequisites: this repo's `publish` job has pushed `:main`; the GHCR packages are public
(`docker logout ghcr.io && docker pull ghcr.io/solvro/backend-mcp-auth:main` works); your SSH key is
in `mlai`'s `~/.ssh/authorized_keys`.

```bash
# 1. Provision (Docker, deploy user, directories, agent, units, network, ssh, firewall)
ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh

# 2. Secrets and env files (on the VM). Prints file names only.
sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh
sudoedit /etc/ml-mcp/secrets/openai_api_key          # and the other empty ones you use
sudoedit /opt/mcpwr/stacks/backend/.env.prod         # FRONTEND_URL, SMTP_*
sudoedit /opt/mcpwr/stacks/ml-mcp/.env               # LLM keys, LANGFUSE_*

# 3. Start deploying
sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main --enable-timers
journalctl -fu mcpwr-deploy@backend

# 4. Checkpoint - from your laptop on the VPN; then tell devops the edge is up
curl -fsS http://10.21.36.20/health/live
```

The frontend's ticks fail pre-flight (`required network backend-mcp_backend does not exist yet`)
until the backend's first deploy has created it; that is expected and retried every minute.

**Load the knowledge graph** (a fresh Neo4j volume is empty; the dump is not in git):

```bash
# laptop, in the ml-mcp checkout
scp dumps/graph_export.cypher mlai@10.21.36.20:/tmp/graph_export.cypher
# VM
sha=$(sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy ml-mcp status | awk '/^deployed:/ {print $2}')
sudo git clone -q https://github.com/Solvro/ml-mcp.git /tmp/ml-mcp && sudo git -C /tmp/ml-mcp checkout -q "$sha"
sudo docker run --rm --network ml-mcp_mcp_network \
  --env-file /opt/mcpwr/stacks/ml-mcp/.env -e NEO4J_URI=bolt://neo4j:7687 \
  -e PIPELINE_HOST_DUMP_DIR=/dump -v /tmp/graph_export.cypher:/dump/graph_export.cypher:ro \
  -v /tmp/ml-mcp:/src -w /src ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv run --frozen restore-graph
sudo rm -rf /tmp/ml-mcp /tmp/graph_export.cypher
```

**Trust Coolify's proxy** once devops has connected `mcpwr.solvro.pl`:

```bash
sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend proxies   # "<count> <peer ip>"
sudoedit /opt/mcpwr/stacks/backend/.env.prod                          # TRUSTED_PROXY_CIDR=<ip>/32
sha=$(sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend status | awk '/^deployed:/ {print $2}')
sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy backend deploy "$sha"   # recreates nginx
```

Until then every visitor shares one rate-limit bucket. In production the BFF sets `Secure`
cookies, so logging in over plain `http://10.21.36.20` does not stick in a browser; it works over
`https://mcpwr.solvro.pl`.

### Day to day

```bash
alias mcpwr='sudo -u mcpwr-deploy /opt/mcpwr/agent/mcpwr-deploy'
mcpwr backend status                  # deployed / previous / bad / paused
mcpwr backend deploy <full sha>       # deploy now (ignores pause and bad marks)
mcpwr backend rollback                # previous release, no migrations
mcpwr pause                           # stop automatic deploys (all stacks); `mcpwr resume`
journalctl -u mcpwr-deploy@backend    # what the agent did
```

Changed something under `deploy/`? Re-run the bootstrap (`--ref main`); the agent does not update
itself.

### Production secrets

`/etc/ml-mcp/secrets` (0750 root:mcpwr-deploy, files 0440), created by `init-secrets.sh`:

```
postgres_password  mongo_root_password  redis_password          generated
database_url  mongo_uri  redis_url                              built from the above
jwt_private_key.pem  jwt_public_key.pem                         generated RS256 pair
jwt_previous_public_key.pem                                     empty until the first rotation
openai_api_key  google_api_key  langfuse_secret_key  smtp_pass  empty = unused; fill by hand
```

Services only ever see `*_FILE=/run/secrets/<name>`. `scripts/check_prod_secrets.py` (run by
`just lint`) fails if the resolved prod config carries a credential as a plain value, publishes a
host port on anything but nginx, or leaves a service without restart policy, resource limits or
a log driver.

### Backups

`mcpwr-backup.timer` (03:00) writes `/var/backups/mcpwr/<date>/postgres.dump` and
`mongo.archive.gz` and keeps a week. They live on the same VM: they cover mistakes and bad
migrations, not losing the machine.

```bash
pg=$(docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=postgres)
mg=$(docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=mongo)
mcpwr pause
# Postgres
sudo docker exec -i "$pg" sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < /var/backups/mcpwr/<date>/postgres.dump
# Mongo
sudo docker exec -i "$mg" sh -c 'mongorestore --quiet --archive --gzip --drop --authenticationDatabase admin -u "$MONGO_INITDB_ROOT_USERNAME" -p "$(cat "$MONGO_INITDB_ROOT_PASSWORD_FILE")"' \
  < /var/backups/mcpwr/<date>/mongo.archive.gz
mcpwr resume
```

### Rotating the JWT keypair

1. `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out new_private.pem`,
   `openssl pkey -in new_private.pem -pubout -out new_public.pem`.
2. In `/etc/ml-mcp/secrets`: `mv jwt_public_key.pem jwt_previous_public_key.pem`; install the new
   pair as `jwt_private_key.pem` / `jwt_public_key.pem` (0440 root:mcpwr-deploy).
3. `mcpwr backend deploy <deployed sha>` (recreates auth- and chat-service). Tokens signed by the
   old key keep verifying via their `kid`.
4. After `REFRESH_TOKEN_EXPIRE_DAYS` (7), truncate `jwt_previous_public_key.pem` and redeploy.

### TLS

Production TLS belongs to Coolify. The self-signed pair from `just tls-selfsigned` only serves
`just up`.
````

- [ ] **Step 6: Run the whole suite and the linters**

Run: `uv run ruff check . && just lint-sh && uv run python scripts/check_prod_secrets.py && just test`
Expected: ruff clean, shellcheck clean, `production stack: 7 services, …`, and the full suite green
(the new `tests/deploy` tests included; `test_deploy_nginx_edge.py` needs Docker running).

- [ ] **Step 7: Commit and open the PR**

```bash
git add deploy/vm/bootstrap.sh tests/deploy/test_deploy_bootstrap.py docs/deploy.md
git update-index --chmod=+x deploy/vm/bootstrap.sh
git commit -m "feat(deploy): vm bootstrap script and production runbook" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Then push `feat/vm-cd` and open a PR against `main` (description ends with
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`) — only when the human asks.

---

# Part B — ml-mcp (branch `feat/vm-cd` in `/Users/domin/Documents/solvro/ml-mcp`)

### Task B1: Production compose overlay and `.env.prod.example`

Spec §6 (ml-mcp bullet) and §9.

**Files:**
- Create: `docker/compose.prod.yml`, `.env.prod.example`
- Test: `tests/test_compose_prod.py`

**Interfaces:**
- Consumes: `docker/compose.stack.yml` (services `neo4j`, `mcp-server`; network `solvro_internal` external `solvro-mcp-internal`).
- Produces: what backend-mcp's `deploy/agent/stacks/ml-mcp.conf` runs: `-p ml-mcp --env-file /opt/mcpwr/stacks/ml-mcp/.env -f docker/compose.stack.yml -f docker/compose.prod.yml` with `RELEASE_TAG`; `.env.prod.example` fetched by `init-secrets.sh` from `https://raw.githubusercontent.com/Solvro/ml-mcp/main/.env.prod.example`.

- [ ] **Step 1: Write the failing test**

`tests/test_compose_prod.py`:

```python
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    if shutil.which("docker") is None:
        pytest.skip("needs the docker CLI for `docker compose config`")
    env_file = tmp_path_factory.mktemp("vm") / ".env"
    env_file.write_text((ROOT / ".env.prod.example").read_text().replace("NEO4J_PASSWORD=", "NEO4J_PASSWORD=x"))
    out = subprocess.run(
        [
            "docker", "compose", "-p", "ml-mcp", "--env-file", str(env_file),
            "-f", str(ROOT / "docker/compose.stack.yml"), "-f", str(ROOT / "docker/compose.prod.yml"),
            "config", "--format", "json",
        ],
        env={**os.environ, "RELEASE_TAG": "sha-check", "ML_MCP_ENV_FILE": str(env_file)},
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)["services"], env_file


@pytest.fixture(scope="module")
def services(rendered) -> dict:
    return rendered[0]


def test_mcp_server_runs_the_published_image(services):
    assert services["mcp-server"]["image"] == "ghcr.io/solvro/ml-mcp-server:sha-check"


def test_mcp_server_reads_only_the_vm_env_file(rendered):
    services, env_file = rendered
    # Older compose prints env_file entries as strings, newer as {"path": ..., "required": ...}.
    paths = [e["path"] if isinstance(e, dict) else e for e in services["mcp-server"]["env_file"]]
    assert paths == [str(env_file)]


def test_nothing_publishes_a_host_port(services):
    assert all(not svc.get("ports") for svc in services.values())


def test_every_service_rotates_its_logs(services):
    for name, svc in services.items():
        assert svc["logging"]["driver"] == "json-file", name
        assert svc["logging"]["options"]["max-size"] == "10m", name


def test_example_lists_only_what_serving_needs():
    keys = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.prod.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert {"NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "OPENAI_API_KEY", "LOG_LEVEL"} <= keys
    assert not any(k.startswith(("PREFECT_", "DATA_PIPELINE_", "OCR_", "PIPELINE_")) for k in keys)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_compose_prod.py -v`
Expected: FAIL — `.env.prod.example` / `docker/compose.prod.yml` missing.

- [ ] **Step 3: Write the files**

`docker/compose.prod.yml`:

```yaml
# Production overlay for the mcpwr VM. The deploy agent in Solvro/backend-mcp runs
#   RELEASE_TAG=sha-<commit> docker compose -p ml-mcp --env-file /opt/mcpwr/stacks/ml-mcp/.env \
#     -f docker/compose.stack.yml -f docker/compose.prod.yml up -d --no-build --wait
# Images come from GHCR; nothing is built on the VM. See backend-mcp docs/deploy.md.
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"

services:
  neo4j:
    logging: *logging

  mcp-server:
    image: ghcr.io/solvro/ml-mcp-server:${RELEASE_TAG:?set by the deploy agent}
    # The base file reads ../.env (a developer's); on the VM the env lives outside the checkout.
    env_file: !override
      - ${ML_MCP_ENV_FILE:-/opt/mcpwr/stacks/ml-mcp/.env}
    logging: *logging
```

`.env.prod.example`:

```bash
# Production env for the mcpwr VM (copied to /opt/mcpwr/stacks/ml-mcp/.env by backend-mcp's
# deploy/vm/init-secrets.sh, which also generates NEO4J_PASSWORD). Serving only: the Prefect/OCR
# ingest pipeline does not run on that machine.

# Neo4j (compose.stack.yml overrides NEO4J_URI to the service name inside the network)
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=

# LLM providers (fill the ones in use)
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
GOOGLE_API_KEY=
CLARIN_API_KEY=

LOG_LEVEL=INFO

# Langfuse (optional)
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_compose_prod.py -v && just test`
Expected: 5 passed; the existing suite still green.

- [ ] **Step 5: Commit**

```bash
git add docker/compose.prod.yml .env.prod.example tests/test_compose_prod.py
git commit -m "feat(deploy): production compose overlay for the mcpwr vm" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task B2: Publish `ghcr.io/solvro/ml-mcp-server` on `main`

Spec §6.

**Files:**
- Modify: `.github/workflows/main.yaml` (new `publish` job; the workflow's top-level `permissions: contents: read` stays, the job raises its own)

**Interfaces:**
- Consumes: job ids `lint`, `test`, `build-check`, `integration`.
- Produces: `ghcr.io/solvro/ml-mcp-server:sha-<sha>` then `:main`.

- [ ] **Step 1: Add the job**

Append to `jobs:`:

```yaml
  # ── 5. Publish — the image the mcpwr VM's deploy agent pulls ─────────────────
  publish:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    needs: [lint, test, build-check, integration]
    permissions:
      contents: read
      packages: write
    # Queued, never parallel: an older run finishing last would move `main` backwards.
    concurrency:
      group: publish-main
      cancel-in-progress: false
    env:
      IMAGE: ghcr.io/solvro/ml-mcp-server
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.mcp
          platforms: linux/amd64
          push: true
          tags: ${{ env.IMAGE }}:sha-${{ github.sha }}
          labels: |
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
          cache-from: type=gha,scope=ml-mcp-server
          cache-to: type=gha,mode=max,scope=ml-mcp-server

      - name: Check the image carries no keys, env files or dumps
        run: |
          ref="$IMAGE:sha-$GITHUB_SHA"
          docker pull -q "$ref" >/dev/null
          found=$(docker run --rm --entrypoint sh "$ref" -c \
            'find /app -path /app/.venv -prune -o \( -name "*.pem" -o -name "*.key" -o -name ".env" -o -name ".env.*" -o -name "*.cypher" \) -print')
          if [ -n "$found" ]; then
            echo "::error::the image contains key, env or dump files:"
            echo "$found"
            exit 1
          fi

      - name: Move the main tag
        run: docker buildx imagetools create -t "$IMAGE:main" "$IMAGE:sha-$GITHUB_SHA"
```

- [ ] **Step 2: Lint and check the image locally**

Run:
```bash
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest .github/workflows/main.yaml
docker build -f docker/Dockerfile.mcp -t ml-mcp-server:check .
docker run --rm --entrypoint sh ml-mcp-server:check -c 'find /app -path /app/.venv -prune -o \( -name "*.pem" -o -name "*.key" -o -name ".env" -o -name ".env.*" -o -name "*.cypher" \) -print'
```
Expected: no actionlint findings for `publish`; the `find` prints nothing. If the image's workdir is not `/app`, read `docker/Dockerfile.mcp`'s `WORKDIR` and use it in both the workflow and this command.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/main.yaml
git commit -m "ci(deploy): publish the mcp server image to ghcr on main" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Part C — frontend-mcp (branch `feat/vm-cd` in `/Users/domin/Documents/solvro/frontend-mcp`)

### Task C1: Standalone build, Dockerfile, healthcheck

Spec §6 (frontend bullet).

**Files:**
- Modify: `next.config.ts`
- Create: `Dockerfile`, `.dockerignore`

**Interfaces:**
- Produces: an image listening on `0.0.0.0:3000`, running as a non-root user, with a `HEALTHCHECK` on `GET /`; reads `AUTH_SERVICE_URL` / `CHAT_SERVICE_URL` at runtime (`lib/server/backend.ts`).

- [ ] **Step 1: Confirm the baseline**

Run: `npm ci && npm run lint && npm test && npm run build`
Expected: all succeed. If `npm run lint` fails on existing code, fix those findings in a separate
commit first (`fix(lint): …`) — CI (Task C2) runs it.

- [ ] **Step 2: Switch on standalone output**

`next.config.ts`:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server (.next/standalone) for the Docker image; see Dockerfile.
  output: "standalone",
};

export default nextConfig;
```

- [ ] **Step 3: Write the Dockerfile and `.dockerignore`**

`Dockerfile`:

```dockerfile
# syntax=docker/dockerfile:1
# Production image for the mcpwr VM (deployed by Solvro/backend-mcp's deploy agent).

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S -G app app
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/ >/dev/null || exit 1
CMD ["node", "server.js"]
```

`.dockerignore`:

```
node_modules
.next
.git
.env*
!.env.example
coverage
graphify-out
docs
*.pem
Dockerfile
.dockerignore
```

- [ ] **Step 4: Build and run it**

Run:
```bash
docker build -t frontend-mcp:check .
docker run -d --name fe-check -p 3300:3000 -e AUTH_SERVICE_URL=http://auth.invalid -e CHAT_SERVICE_URL=http://chat.invalid frontend-mcp:check
sleep 25
curl -fsS -o /dev/null -w '%{http_code}\n' http://localhost:3300/
docker inspect --format '{{.State.Health.Status}} {{.Config.User}}' fe-check
docker run --rm --entrypoint sh frontend-mcp:check -c 'find /app -path /app/node_modules -prune -o \( -name "*.pem" -o -name ".env" -o -name ".env.*" \) -print'
docker rm -f fe-check
```
Expected: `200`; `healthy app`; the `find` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add next.config.ts Dockerfile .dockerignore
git commit -m "build(docker): standalone next image for the mcpwr vm" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task C2: CI and publish workflow

Spec §6 (frontend row).

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: `ghcr.io/solvro/frontend-mcp:sha-<sha>` then `:main`, only after lint, tests and build pass on a push to `main`.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: ["main"]
  pull_request:
    branches: ["main"]

permissions:
  contents: read

jobs:
  check:
    name: Lint, test, build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
      - run: npm ci
      - run: npm run lint
      - run: npm test
      - run: npm run build

  publish:
    name: Publish image
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    needs: [check]
    permissions:
      contents: read
      packages: write
    # Queued, never parallel: an older run finishing last would move `main` backwards.
    concurrency:
      group: publish-main
      cancel-in-progress: false
    env:
      IMAGE: ghcr.io/solvro/frontend-mcp
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: true
          tags: ${{ env.IMAGE }}:sha-${{ github.sha }}
          labels: |
            org.opencontainers.image.revision=${{ github.sha }}
            org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}
          cache-from: type=gha,scope=frontend-mcp
          cache-to: type=gha,mode=max,scope=frontend-mcp
      - name: Check the image carries no keys or env files
        run: |
          ref="$IMAGE:sha-$GITHUB_SHA"
          docker pull -q "$ref" >/dev/null
          found=$(docker run --rm --entrypoint sh "$ref" -c \
            'find /app -path /app/node_modules -prune -o \( -name "*.pem" -o -name ".env" -o -name ".env.*" \) -print')
          if [ -n "$found" ]; then
            echo "::error::the image contains key or env files:"
            echo "$found"
            exit 1
          fi
      - name: Move the main tag
        run: docker buildx imagetools create -t "$IMAGE:main" "$IMAGE:sha-$GITHUB_SHA"
```

- [ ] **Step 2: Lint the workflow**

Run: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest .github/workflows/ci.yml`
Expected: no findings.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(deploy): lint, test, build and publish the frontend image" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task C3: `deploy/compose.yml` for the VM

Spec §6 (frontend bullet) and §8 (fixed IP, alias).

**Files:**
- Create: `deploy/compose.yml`

**Interfaces:**
- Consumes: external network `backend-mcp_backend` (subnet `10.89.0.0/24`, created by the backend stack); service names `auth-service`, `chat-service` on port 8000.
- Produces: container reachable as `frontend:3000` at `10.89.0.11` — what the backend's nginx (`templates.proxy`) and `FORWARDED_ALLOW_IPS` expect.

- [ ] **Step 1: Write the file**

`deploy/compose.yml`:

```yaml
# The frontend on the mcpwr VM. Run by Solvro/backend-mcp's deploy agent:
#   RELEASE_TAG=sha-<commit> docker compose -p frontend-mcp -f deploy/compose.yml up -d --no-build --wait
# It joins the backend's network at a fixed address, which auth- and chat-service trust for
# X-Forwarded-For (FORWARDED_ALLOW_IPS), and the backend's nginx reaches it as `frontend`.
services:
  frontend:
    image: ghcr.io/solvro/frontend-mcp:${RELEASE_TAG:?set by the deploy agent}
    environment:
      NODE_ENV: production
      AUTH_SERVICE_URL: http://auth-service:8000
      CHAT_SERVICE_URL: http://chat-service:8000
    restart: unless-stopped
    read_only: true
    tmpfs: [/tmp, /app/.next/cache]
    stop_grace_period: 20s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }
    deploy:
      resources:
        limits: { cpus: "1.0", memory: 512m }
    networks:
      backend:
        ipv4_address: 10.89.0.11
        aliases: [frontend]

networks:
  backend:
    external: true
    name: backend-mcp_backend
```

- [ ] **Step 2: Validate it**

Run: `RELEASE_TAG=sha-check docker compose -p frontend-mcp -f deploy/compose.yml config --quiet && echo ok`
Expected: `ok`.

- [ ] **Step 3: Run it against a real backend network**

Run:
```bash
docker network create --subnet 10.89.0.0/24 --ip-range 10.89.0.128/25 backend-mcp_backend
docker tag frontend-mcp:check ghcr.io/solvro/frontend-mcp:sha-check   # the image from Task C1
RELEASE_TAG=sha-check docker compose -p frontend-mcp -f deploy/compose.yml up -d --no-build --wait
docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' frontend-mcp-frontend-1
docker run --rm --network backend-mcp_backend curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}\n' http://frontend:3000/
RELEASE_TAG=sha-check docker compose -p frontend-mcp -f deploy/compose.yml down
docker network rm backend-mcp_backend
docker rmi ghcr.io/solvro/frontend-mcp:sha-check
```
Expected: `--wait` returns once healthy; `10.89.0.11`; `200`. (If a local backend-mcp stack already
created `ml-mcp-backend_backend` on `10.89.0.0/24`, stop it first: the subnets would overlap.)

- [ ] **Step 4: Commit**

```bash
git add deploy/compose.yml
git commit -m "feat(deploy): compose file for the frontend on the mcpwr vm" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Part D — VM bring-up (with the human, on the VPN)

Every step here runs against the real VM. The implementer runs commands over SSH only when the
human is connected to the VPN and asks for it; **the human types every secret value** (sudoedit
on the VM) — they are never pasted into the conversation.

### Task D1: Reachability and preconditions

- [ ] **Step 1:** `ssh -o ConnectTimeout=8 mlai@10.21.36.20 'uname -m; . /etc/os-release; echo "$PRETTY_NAME"; nproc; free -h | head -2; df -h / | tail -1'`
  Expected: `x86_64`, Ubuntu or Debian, and resources near the spec's 4 vCPU / 16 GB / 80 GB. `aarch64` means adding `linux/arm64` to all four publish jobs before continuing.
- [ ] **Step 2:** the human's public key is in `mlai`'s `~/.ssh/authorized_keys` (`ssh-copy-id mlai@10.21.36.20` from the laptop) — otherwise the bootstrap keeps password login on and says so.
- [ ] **Step 3:** Part A is merged, `publish` succeeded, and the packages are public:
  `docker logout ghcr.io; docker pull ghcr.io/solvro/backend-mcp-auth:main && docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' ghcr.io/solvro/backend-mcp-auth:main`
  Expected: the pull works anonymously and prints the merge commit's sha. If the pull is denied, a Solvro package admin must make `backend-mcp-auth` and `backend-mcp-chat` public (spec P3).

### Task D2: Provision

- [ ] **Step 1:** `ssh mlai@10.21.36.20 'sudo bash -s -- --ref main' < deploy/vm/bootstrap.sh`
  Expected: ends with `==> done` and the "Next:" block.
- [ ] **Step 2:** verify on the VM:
  ```bash
  docker version --format '{{.Server.Version}}' && docker compose version
  id mcpwr-deploy
  docker network inspect solvro-mcp-internal --format '{{.Internal}}'
  ls -l /opt/mcpwr/agent /etc/systemd/system/mcpwr-*
  sudo ufw status | head -5
  ```
  Expected: compose ≥ 2.24; `mcpwr-deploy` in group `docker`; `true`; agent, backup script, three `.conf`, four units; `Status: active`, 22 allowed.
- [ ] **Step 3:** re-run the same command — nothing breaks, no errors (idempotence).

### Task D3: Secrets and config (human types the values)

- [ ] **Step 1:** `sudo /opt/mcpwr/src/backend-mcp/deploy/vm/init-secrets.sh` — expect `created …` lines only.
- [ ] **Step 2:** the human fills, with `sudoedit`: the LLM/Langfuse/SMTP secret files they use, `FRONTEND_URL`/`SMTP_*` in `/opt/mcpwr/stacks/backend/.env.prod`, the LLM keys in `/opt/mcpwr/stacks/ml-mcp/.env`. SMTP unknown yet (spec P5): leave `SMTP_HOST` as the example value and note that verification emails fail until it is set.
- [ ] **Step 3:** re-run `init-secrets.sh` — every line says `kept`.

### Task D4: First deploy and the devops checkpoint

- [ ] **Step 1:** `sudo /opt/mcpwr/src/backend-mcp/deploy/vm/bootstrap.sh --ref main --enable-timers`
- [ ] **Step 2:** `journalctl -fu mcpwr-deploy@backend` until `report stack=backend event=success`.
  The frontend/ml-mcp timers log pre-flight retries until their images exist (Parts B, C) — expected.
- [ ] **Step 3:** from the laptop on the VPN: `curl -fsS http://10.21.36.20/health/live` → `{"status":"ok",…}`; `curl -s -o /dev/null -w '%{http_code}\n' http://10.21.36.20/api/chat` → `502` (frontend not deployed yet) or `404` (once it is) — never a chat-service response.
- [ ] **Step 4:** ✅ **Tell devops** `http://10.21.36.20:80` is up, with `/health/live` as the health endpoint (spec P4).

### Task D5: Agent behaviour on the real box

- [ ] **Step 1:** idle tick: `sudo systemctl start mcpwr-deploy@backend.service; journalctl -u mcpwr-deploy@backend -n 5` — no pull, no deploy.
- [ ] **Step 2:** merge any small backend PR; within ~2 min of its publish job the journal shows `event=success` for the new sha, and `mcpwr backend status` shows the old sha as `previous`.
- [ ] **Step 3:** `mcpwr backend rollback` → previous sha running, `/health/live` still 200; then `mcpwr backend deploy <newest sha>` restores it.
- [ ] **Step 4:** `mcpwr pause`, merge another PR, confirm nothing deploys; `mcpwr resume` → it deploys.
- [ ] **Step 5:** `sudo reboot`; afterwards all containers are back, timers listed in `systemctl list-timers 'mcpwr-*'`, `/health/live` 200.
  (Automatic rollback on a failed gate is proven by Task A3's tests; forcing a broken release onto `main` just to watch it is not worth it.)

### Task D6: ml-mcp and frontend arrive (after Parts B and C merge)

- [ ] **Step 1:** packages `ml-mcp-server` and `frontend-mcp` public (spec P3); `journalctl -u mcpwr-deploy@ml-mcp -u mcpwr-deploy@frontend` show `event=success`.
- [ ] **Step 2:** `docker ps --format '{{.Names}} {{.Status}}'` — `ml-mcp-neo4j`, `ml-mcp-server`, `frontend-mcp-frontend-1` healthy; `curl -s http://10.21.36.20/health` shows the `mcp` check `up`.
- [ ] **Step 3:** load the graph — the "Load the knowledge graph" block in `docs/deploy.md` (the human `scp`s the dump). Then a chat question through the frontend (browser, once the domain works) gets a knowledge-graph answer.

### Task D7: Backups

- [ ] **Step 1:** `sudo systemctl start mcpwr-backup.service && sudo ls -l /var/backups/mcpwr/$(date -u +%F)` — both files, `-rw-------`.
- [ ] **Step 2:** restore check into a scratch database:
  ```bash
  pg=$(docker ps -q --filter label=com.docker.compose.project=backend-mcp --filter label=com.docker.compose.service=postgres)
  sudo docker exec "$pg" sh -c 'createdb -U "$POSTGRES_USER" restore_check'
  sudo docker exec -i "$pg" sh -c 'pg_restore -U "$POSTGRES_USER" -d restore_check' < /var/backups/mcpwr/$(date -u +%F)/postgres.dump
  sudo docker exec "$pg" sh -c 'psql -U "$POSTGRES_USER" -d restore_check -c "\dt"'
  sudo docker exec "$pg" sh -c 'dropdb -U "$POSTGRES_USER" restore_check'
  ```
  Expected: the `users`/`roles`/`refresh_tokens`/`alembic_version` tables are listed.
- [ ] **Step 3:** `mg=…` (service `mongo`) and `mongorestore --dryRun` with the archive — exits 0.

### Task D8: Real client IPs (after devops connects)

- [ ] **Step 1:** `mcpwr backend proxies` → the Coolify proxy's address with the highest count.
- [ ] **Step 2:** set `TRUSTED_PROXY_CIDR=<ip>/32` in `.env.prod`, `mcpwr backend deploy <deployed sha>`.
- [ ] **Step 3:** request `https://mcpwr.solvro.pl/health/live` from the laptop; the newest nginx log line shows `peer=<coolify ip>` and the laptop's public IP as `$remote_addr`.
- [ ] **Step 4:** only after the frontend's P1 fix (BFF forwards `X-Forwarded-For` on `/bff/auth/*`) is deployed: log in with a wrong password 11 times from one machine → the 11th is `429`; another machine can still log in. Before P1 this is **expected to fail** (one site-wide bucket) — do not announce the public launch until it passes.

### Task D9: Admin follow-ups to hand over

- [ ] Spec P6: ask a repo admin to make the `E2E tests` check required on `main` in backend-mcp.
- [ ] Spec P5: SMTP host/credentials from Solvro → `SMTP_*` + `smtp_pass`, `mcpwr backend deploy <sha>`, register a test account, the email arrives.
- [ ] File frontend tickets P1 and P2 (texts in the spec §11) if they do not exist yet.
