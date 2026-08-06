from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.secrets import provider


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "app"
    log_level: str = "INFO"

    database_url: str = Field(
        default_factory=lambda: provider.get("DATABASE_URL", ""),
    )
    redis_url: str = Field(
        default_factory=lambda: provider.get("REDIS_URL", ""),
    )
