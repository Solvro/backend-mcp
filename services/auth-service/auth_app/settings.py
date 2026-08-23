from functools import lru_cache

from common.settings import CommonSettings


class AuthSettings(CommonSettings):
    app_name: str = "auth-service"

    access_token_expire_minutes: int = 30

    argon2_time_cost: int = 3
    argon2_memory_cost: int = 64 * 1024
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32
    argon2_salt_len: int = 16
    max_password_length: int = 128
    rate_limit: int = 5
    rate_limit_window_seconds: int = 60


@lru_cache
def get_settings() -> AuthSettings:
    return AuthSettings()
