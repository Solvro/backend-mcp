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
    redis_key_prefix: str = "mcp"

    rate_limit_enabled: bool = True
    rate_limit: int = 60
    rate_limit_window_seconds: int = 60
    mongo_uri: str = Field(
        default_factory=lambda: get_secrets_provider().get("MONGO_URI", ""),
    )
    error_type_base_url: str = Field(
        default_factory=lambda: get_secrets_provider().get(
            "ERROR_TYPE_BASE_URL", ""
            ),
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

    cors_allow_origins: list[str] = [
        "http://localhost:8000",
        "http://localhost:8080",
    ]
    cors_allow_methods: list[str] = ["*"]
    cors_allow_headers: list[str] = ["*"]
    cors_allow_credentials: bool = True
    cors_expose_headers: list[str] = [
        "x-request-id",
        "x-trace-id",
        "x-process-time",
        "x-session-id",
        "x-user-id",
    ]
