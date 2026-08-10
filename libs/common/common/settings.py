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
    mongo_uri: str = Field(
        default_factory=lambda: get_secrets_provider().get("MONGO_URI", ""),
    )
    error_type_base_url: str = Field(
        default_factory=lambda: get_secrets_provider().get("ERROR_TYPE_BASE_URL", ""),
    )

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    db_echo: bool = False

    mongo_db_name: str = "mcp_backend"
    mongo_max_pool_size: int = 100
    mongo_min_pool_size: int = 0
    mongo_server_selection_timeout_ms: int = 5000
