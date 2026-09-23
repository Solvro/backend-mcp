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
EMPTY = [
    "jwt_previous_public_key.pem",
    "openai_api_key",
    "google_api_key",
    "langfuse_secret_key",
    "smtp_pass",
]
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
    lines = path.read_text().splitlines()
    return next(line.split("=", 1)[1] for line in lines if line.startswith(f"{key}="))


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
    user = env_value(layout.backend_env, "POSTGRES_USER")
    db = env_value(layout.backend_env, "POSTGRES_DB")
    mongo_user = env_value(layout.backend_env, "MONGO_ROOT_USER")
    database_url = (layout.secrets / "database_url").read_text()
    mongo_uri = (layout.secrets / "mongo_uri").read_text()
    redis_url = (layout.secrets / "redis_url").read_text()
    assert database_url == f"postgresql+asyncpg://{user}:{pg}@postgres:5432/{db}"
    assert mongo_uri == f"mongodb://{mongo_user}:{mongo}@mongo:27017/?authSource=admin"
    assert redis_url == f"redis://:{redis}@redis:6379"


def test_jwt_keypair_matches(layout):
    assert layout.run().returncode == 0

    private_bytes = (layout.secrets / "jwt_private_key.pem").read_bytes()
    public_bytes = (layout.secrets / "jwt_public_key.pem").read_bytes()
    private = serialization.load_pem_private_key(private_bytes, None)
    public = serialization.load_pem_public_key(public_bytes)
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


def test_generation_failure_leaves_no_partial_secret(layout, tmp_path):
    # A NEO4J_PASSWORD already set skips the earlier generator call, so the fake openssl below
    # is first hit by the postgres_password loop, matching the reviewer's reproduction.
    layout.ml_env.parent.mkdir(parents=True)
    layout.ml_env.write_text("NEO4J_PASSWORD=chosen-by-a-human\n")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_openssl = fake_bin / "openssl"
    fake_openssl.write_text("#!/bin/sh\nexit 1\n")
    fake_openssl.chmod(0o755)

    failing = layout.run(PATH=f"{fake_bin}:{os.environ['PATH']}")

    assert failing.returncode != 0
    assert not (layout.secrets / "postgres_password").exists()

    result = layout.run()

    assert result.returncode == 0, result.stderr
    assert HEX64.match((layout.secrets / "postgres_password").read_text())
    pg_password = (layout.secrets / "postgres_password").read_text()
    assert pg_password in (layout.secrets / "database_url").read_text()


def test_missing_url_file_is_rebuilt_without_rotating_the_password(layout):
    assert layout.run().returncode == 0
    pg_password = (layout.secrets / "postgres_password").read_text()
    (layout.secrets / "database_url").unlink()

    result = layout.run()

    assert result.returncode == 0, result.stderr
    assert (layout.secrets / "postgres_password").read_text() == pg_password
    assert pg_password in (layout.secrets / "database_url").read_text()


def test_existing_empty_password_is_refused_not_silently_kept(layout):
    layout.secrets.mkdir(parents=True)
    (layout.secrets / "redis_password").write_text("")

    result = layout.run()

    assert result.returncode != 0
    assert str(layout.secrets / "redis_password") in result.stderr
    assert not (layout.secrets / "redis_url").exists()


def test_only_one_half_of_jwt_pair_is_refused(layout):
    layout.secrets.mkdir(parents=True)
    (layout.secrets / "jwt_private_key.pem").write_text("not a real key\n")

    result = layout.run()

    assert result.returncode != 0
    assert not (layout.secrets / "jwt_public_key.pem").exists()
    assert (layout.secrets / "jwt_private_key.pem").read_text() == "not a real key\n"


def test_existing_ml_env_with_empty_neo4j_password_is_filled_in_place(layout):
    layout.ml_env.parent.mkdir(parents=True)
    layout.ml_env.write_text(
        "NEO4J_URI=bolt://neo4j:7687\nNEO4J_USER=neo4j\nNEO4J_PASSWORD=\nOPENAI_API_KEY=set-by-human\n"
    )

    assert layout.run().returncode == 0

    text = layout.ml_env.read_text()
    assert "NEO4J_URI=bolt://neo4j:7687" in text
    assert "NEO4J_USER=neo4j" in text
    assert "OPENAI_API_KEY=set-by-human" in text
    assert HEX64.match(env_value(layout.ml_env, "NEO4J_PASSWORD"))
