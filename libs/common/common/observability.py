import logging
from typing import TYPE_CHECKING, Any

from common.settings import CommonSettings

if TYPE_CHECKING:
    from langfuse import Langfuse

logger = logging.getLogger(__name__)

_client: "Langfuse | None" = None
_initialized = False


def get_langfuse(settings: CommonSettings | None = None) -> "Langfuse | None":
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True

    s = settings or CommonSettings()
    if not (s.langfuse_secret_key and s.langfuse_public_key):
        logger.info("Langfuse disabled (LANGFUSE_SECRET_KEY/PUBLIC_KEY not both set)")
        return None

    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "Langfuse keys are set but the langfuse package is not installed -> "
            "tracing disabled",
        )
        return None

    try:
        _client = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            host=s.langfuse_host,
        )
    except Exception:
        logger.warning("Langfuse initialisation failed -> tracing disabled", exc_info=True)
        _client = None
        return None

    logger.info("Langfuse enabled (host=%s)", s.langfuse_host)
    return _client


def is_langfuse_enabled(settings: CommonSettings | None = None) -> bool:
    return get_langfuse(settings) is not None


def shutdown_langfuse() -> None:
    global _client, _initialized
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:
            logger.debug("Langfuse shutdown failed", exc_info=True)
    _client = None
    _initialized = False


def _reset_for_tests(client: Any = None, initialized: bool = False) -> None:
    global _client, _initialized
    _client = client
    _initialized = initialized
