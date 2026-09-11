from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Final

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from auth_app.settings import AuthSettings, get_settings

_DEFAULT_ARGON2_TYPE: Final[Type] = Type.ID


def create_access_token(user_id: int | str, expires_delta: timedelta | None = None) -> str:
    """Generates JWT access token for the user."""
    settings = get_settings()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire_minutes = getattr(settings, "access_token_expire_minutes", 30)
        expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)

    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }

    algorithm = getattr(settings, "jwt_algorithm", "HS256")
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=algorithm)


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
