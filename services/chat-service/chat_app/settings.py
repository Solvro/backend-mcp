from functools import lru_cache

from common.secrets import get_secrets_provider
from common.settings import CommonSettings
from pydantic import Field


class ChatSettings(CommonSettings):
    app_name: str = "chat-service"

    mcp_server_url: str = "http://localhost:8005/mcp"

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
