import os
from abc import ABC, abstractmethod
from pathlib import Path


class SecretsProvider(ABC):
    @abstractmethod
    def get(self, key: str, default: str | None = None) -> str | None:
        raise NotImplementedError


class EnvSecretsProvider(SecretsProvider):
    def get(self, key: str, default: str | None = None) -> str | None:
        value = os.getenv(key)
        if value not in (None, ""):
            return value

        file_path = os.getenv(f"{key}_FILE")
        if file_path:
            path = Path(file_path)
            if path.exists():
                return path.read_text(encoding="utf-8").strip()

        return default

provider = EnvSecretsProvider()
