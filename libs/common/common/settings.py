from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.secrets import get_secrets_provider


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "app"
    log_level: str = "INFO"

    database_url: str = Field(
        default_factory=lambda: get_secrets_provider().get("DATABASE_URL", ""),
    )
    redis_url: str = Field(
        default_factory=lambda: get_secrets_provider().get("REDIS_URL", ""),
    )

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_echo: bool = False
