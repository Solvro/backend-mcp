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

    rate_limit: int = 5
    rate_limit_window_seconds: int = 60


@lru_cache
def get_settings() -> AuthSettings:
    return AuthSettings()
