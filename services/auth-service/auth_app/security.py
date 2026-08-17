from __future__ import annotations

from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from auth_app.settings import AuthSettings, get_settings

_DEFAULT_ARGON2_TYPE: Final[Type] = Type.ID

class PasswordManager:
    """Manages password hashing and verification using Argon2."""

    def __init__(self, settings: AuthSettings | None = None):
        s = settings or get_settings()
        self._hasher = PasswordHasher(
            time_cost=s.argon2_time_cost,
            memory_cost=s.argon2_memory_cost,
            parallelism=s.argon2_parallelism,
            hash_len=s.argon2_hash_len,
            salt_len=s.argon2_salt_len,
            type=_DEFAULT_ARGON2_TYPE,
        )

    def hash_password(self, password: str) -> str:
        if not password:
            raise ValueError("Password cannot be empty.")
        if len(password) > 128:
            raise ValueError("Password exceeds maximum allowed length.")
        return self._hasher.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        if not password or len(password) > 128:
            return False
        try:
            self._hasher.verify(password_hash, password)
            return True
        except (InvalidHashError, VerificationError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return bool(self._hasher.check_needs_rehash(password_hash))
        except InvalidHashError:
            return False

password_manager = PasswordManager()
