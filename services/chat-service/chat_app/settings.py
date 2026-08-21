from functools import lru_cache

from common.secrets import get_secrets_provider
from common.settings import CommonSettings
from pydantic import Field


class ChatSettings(CommonSettings):
    app_name: str = "chat-service"

    mcp_server_url: str = "http://localhost:8005/mcp"
    mcp_timeout_seconds: float = 15.0
    mcp_init_timeout_seconds: float = 10.0
    mcp_max_retries: int = 2
    mcp_retry_base_delay: float = 0.2
    mcp_retry_max_delay: float = 2.0

    mcp_breaker_enabled: bool = True
    mcp_breaker_failure_threshold: int = 5
    mcp_breaker_reset_timeout_seconds: float = 30.0

    context_max_messages: int = 6
    context_max_chars: int = 8000

    rate_limit: int = 30
    rate_limit_window_seconds: int = 60

    chat_input_min_length: int = 1
    chat_input_max_length: int = 2000

    openai_api_key: str = Field(
        default_factory=lambda: get_secrets_provider().get("OPENAI_API_KEY", ""),
    )
    google_api_key: str = Field(
        default_factory=lambda: get_secrets_provider().get("GOOGLE_API_KEY", ""),
    )
    clarin_api_key: str = Field(
        default_factory=lambda: get_secrets_provider().get("CLARIN_API_KEY", ""),
    )


@lru_cache
def get_settings() -> ChatSettings:
    return ChatSettings()
