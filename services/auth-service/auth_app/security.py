from __future__ import annotations

from functools import lru_cache
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from auth_app.settings import AuthSettings, get_settings

_DEFAULT_ARGON2_TYPE: Final[Type] = Type.ID

class PasswordManager:
    """Manages password hashing and verification using Argon2."""

    def __init__(self, settings: AuthSettings | None = None):
        self._settings = settings or get_settings()
        self._hasher = PasswordHasher(
            time_cost=self._settings.argon2_time_cost,
            memory_cost=self._settings.argon2_memory_cost,
            parallelism=self._settings.argon2_parallelism,
            hash_len=self._settings.argon2_hash_len,
            salt_len=self._settings.argon2_salt_len,
            type=_DEFAULT_ARGON2_TYPE,
        )

    def hash_password(self, password: str) -> str:
        if not password:
            raise ValueError("Password cannot be empty.")
        if len(password) > self._settings.max_password_length:
            raise ValueError("Password exceeds maximum allowed length.")
        return self._hasher.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        if not isinstance(password_hash, str):
            return False
        if (
            not isinstance(password, str)
            or not password
            or len(password) > self._settings.max_password_length
        ):
            return False
        try:
            self._hasher.verify(password_hash, password)
            return True
        except (InvalidHashError, VerificationError, TypeError, AttributeError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        if not isinstance(password_hash, str):
            return False
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except (InvalidHashError, TypeError, AttributeError):
            return False


@lru_cache(maxsize=1)
def get_password_manager() -> PasswordManager:
    return PasswordManager()
