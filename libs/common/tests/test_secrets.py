from pathlib import Path

import pytest
from common.secrets import EnvSecretsProvider


@pytest.mark.unit
def test_env_has_highest_precedence(tmp_path: Path, monkeypatch) -> None:
    secret = tmp_path / "db.txt"
    secret.write_text("from-file")

    monkeypatch.setenv("DATABASE_URL", "from-env")
    monkeypatch.setenv("DATABASE_URL_FILE", str(secret))

    provider = EnvSecretsProvider()

    assert provider.get("DATABASE_URL", "default") == "from-env"


@pytest.mark.unit
def test_file_used_when_env_missing(tmp_path: Path, monkeypatch) -> None:
    secret = tmp_path / "db.txt"
    secret.write_text("from-file")

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL_FILE", str(secret))

    provider = EnvSecretsProvider()

    assert provider.get("DATABASE_URL", "default") == "from-file"


@pytest.mark.unit
def test_default_used_when_nothing_set(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)

    provider = EnvSecretsProvider()

    assert provider.get("DATABASE_URL", "default") == "default"
