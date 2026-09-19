from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECRET_NAME = re.compile(
    r"(_KEY|_PASS|_PASSWORD|_SECRET|_TOKEN)$|^(DATABASE_URL|REDIS_URL|MONGO_URI)$"
)
ALLOWED_PLAIN = {"LANGFUSE_PUBLIC_KEY"}


def rendered_config() -> dict:
    env = {
        **os.environ,
        "POSTGRES_USER": "u",
        "POSTGRES_DB": "d",
        "MONGO_ROOT_USER": "u",
        "FRONTEND_URL": "https://x",
        "SMTP_HOST": "smtp",
        "SMTP_FROM": "a@x",
        "SERVER_NAME": "x",
        "CORS_ALLOWED_ORIGIN": "https://x",
    }
    with tempfile.TemporaryDirectory() as tmp:
        env["SECRETS_DIR"] = tmp
        out = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "docker/compose.yml"),
                "-f",
                str(ROOT / "docker/compose.prod.yml"),
                "config",
                "--format",
                "json",
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return json.loads(out)


def main() -> int:
    cfg = rendered_config()
    problems: list[str] = []

    for name, svc in cfg["services"].items():
        env = svc.get("environment") or {}
        if isinstance(env, list):  # "K=V" form
            env = dict(item.split("=", 1) if "=" in item else (item, None) for item in env)
        mounted = {s["source"] for s in svc.get("secrets", [])}
        for var, value in env.items():
            if var.endswith("_FILE"):
                if not str(value).startswith("/run/secrets/"):
                    problems.append(f"{name}: {var}={value!r} is not a /run/secrets path")
                elif str(value).rsplit("/", 1)[-1] not in mounted:
                    problems.append(f"{name}: {var} points at a secret the service does not mount")
            elif SECRET_NAME.search(var) and var not in ALLOWED_PLAIN and value not in (None, ""):
                problems.append(f"{name}: {var} is set in plaintext (use {var}_FILE)")
        if svc.get("restart") not in ("unless-stopped", "always", "no", None) and name != "migrate":
            problems.append(f"{name}: unexpected restart policy {svc.get('restart')!r}")
        if name != "migrate" and svc.get("restart") not in ("unless-stopped", "always"):
            problems.append(f"{name}: no restart policy")
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits")
        if name != "migrate" and not limits:
            problems.append(f"{name}: no resource limits")
        if not (svc.get("logging") or {}).get("driver"):
            problems.append(f"{name}: no log driver")
        for port in svc.get("ports") or []:
            if name != "nginx":
                problems.append(f"{name}: publishes host port {port} (only nginx may)")

    if problems:
        print("production stack check failed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    n = len(cfg["services"])
    print(f"production stack: {n} services, no plaintext credentials, all policies present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
