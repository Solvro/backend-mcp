from functools import lru_cache

from common.secrets import get_secrets_provider
from common.settings import CommonSettings
from pydantic import Field


class AuthSettings(CommonSettings):
    app_name: str = "auth-service"

    jwt_secret_key: str = Field(
        default_factory=lambda: get_secrets_provider().get("JWT_SECRET_KEY", ""),
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    argon2_time_cost: int = 3
    argon2_memory_cost: int = 64 * 1024
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32
    argon2_salt_len: int = 16
    max_password_length: int = 128


@lru_cache
def get_settings() -> AuthSettings:
    return AuthSettings()
