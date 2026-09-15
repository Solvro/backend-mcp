from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Final

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from common.auth import ACCESS_TOKEN_TYP, REFRESH_TOKEN_TYP
from common.jwt_keys import is_asymmetric, signing_kid

from auth_app.models import User
from auth_app.settings import AuthSettings, get_settings

_DEFAULT_ARGON2_TYPE: Final[Type] = Type.ID


def create_access_token(user: User, expires_delta: timedelta | None = None) -> str:
    """Generates JWT access token for the user."""
    settings = get_settings()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire_minutes = getattr(settings, "access_token_expire_minutes", 30)
        expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)

    role_names = [role.name for role in user.roles] if user.roles else []

    payload = {
        "sub": str(user.id),
        "roles": role_names,
        "email_verified": user.email_verified,
        "exp": expire,
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": ACCESS_TOKEN_TYP,
    }

    return _sign(payload, settings)


def create_refresh_token(user: User) -> tuple[str, str, datetime]:
    """Generates a long-lived JWT refresh token, returns (token, jti, expires_at)."""
    settings = get_settings()
    jti = str(uuid.uuid4())

    expire_days = getattr(settings, "refresh_token_expire_days", 7)
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

    role_names = [role.name for role in user.roles] if user.roles else []

    payload = {
        "sub": str(user.id),
        "roles": role_names,
        "email_verified": user.email_verified,
        "exp": expires_at,
        "jti": jti,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": REFRESH_TOKEN_TYP,
    }

    return _sign(payload, settings), jti, expires_at


def _sign(payload: dict, settings: AuthSettings) -> str:
    if is_asymmetric(settings.jwt_algorithm):
        return jwt.encode(
            payload,
            settings.jwt_private_key,
            algorithm=settings.jwt_algorithm,
            headers={"kid": signing_kid(settings)},
        )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


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

        self.DUMMY_HASH = self._hasher.hash("dummy_password123")

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
