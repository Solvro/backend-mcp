from __future__ import annotations

from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from auth_app.settings import AuthSettings, get_settings

_DEFAULT_ARGON2_TYPE: Final[Type] = Type.ID


def get_password_hasher(settings: AuthSettings | None = None, *, time_cost: int | None = None,
                       memory_cost: int | None = None, parallelism: int | None = None,
                       hash_len: int | None = None, salt_len: int | None = None) -> PasswordHasher:
    """Build a PasswordHasher from current settings or explicit override parameters."""
    s = settings or get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost if time_cost is None else time_cost,
        memory_cost=s.argon2_memory_cost if memory_cost is None else memory_cost,
        parallelism=s.argon2_parallelism if parallelism is None else parallelism,
        hash_len=s.argon2_hash_len if hash_len is None else hash_len,
        salt_len=s.argon2_salt_len if salt_len is None else salt_len,
        type=_DEFAULT_ARGON2_TYPE,
    )


def hash_password(password: str, *, hasher: PasswordHasher | None = None) -> str:
    """Hashes a password and never stores the plaintext value."""
    if not password:
        raise ValueError("Password cannot be empty.")
    if len(password) > 128:
        raise ValueError("Password exceeds maximum allowed length.")

    return (hasher or get_password_hasher()).hash(password)


def verify_password(password: str, password_hash: str, *,
                    hasher: PasswordHasher | None = None) -> bool:
    """Verifies whether a plaintext password matches a stored Argon2 hash."""
    if not password or len(password) > 128:
        return False

    try:
        (hasher or get_password_hasher()).verify(password_hash, password)
        return True
    except (InvalidHashError, VerificationError):
        return False


def needs_rehash(password_hash: str, *, hasher: PasswordHasher | None = None) -> bool:
    """Returns True when an existing hash does not match the current Argon2 parameters."""
    try:
        return bool((hasher or get_password_hasher()).check_needs_rehash(password_hash))
    except InvalidHashError:
        return False
