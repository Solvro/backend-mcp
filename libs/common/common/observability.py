import hashlib
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from common.context import trace_id_var
from common.settings import CommonSettings

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse._client.span import LangfuseObservationWrapper as Observation

logger = logging.getLogger(__name__)

_client: "Langfuse | None" = None
_initialized = False


def new_trace_id(seed: str | None = None) -> str:
    try:
        from langfuse import Langfuse

        return Langfuse.create_trace_id(seed=seed)
    except ImportError:
        if seed is not None:
            return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return secrets.token_hex(16)


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
            "Langfuse keys are set but the langfuse package is not installed -> tracing disabled",
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


@contextmanager
def use_trace_id(trace_id: str) -> Iterator[str]:
    token = trace_id_var.set(trace_id)
    try:
        yield trace_id
    finally:
        trace_id_var.reset(token)


@contextmanager
def start_span(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    settings: CommonSettings | None = None,
) -> "Iterator[Observation | None]":
    client = get_langfuse(settings)
    if client is None:
        yield None
        return

    with client.start_as_current_observation(
        name=name,
        as_type=as_type,
        input=input,
        metadata=metadata,
    ) as span:
        yield span


@contextmanager
def start_turn_trace(
    trace_id: str,
    *,
    name: str = "chat-turn",
    session_id: str | None = None,
    tags: list[str] | None = None,
    input: Any = None,
    settings: CommonSettings | None = None,
) -> "Iterator[Observation | None]":
    with use_trace_id(trace_id):
        client = get_langfuse(settings)
        if client is None:
            yield None
            return

        from langfuse import propagate_attributes

        with (
            client.start_as_current_observation(
                name=name,
                as_type="span",
                trace_context={"trace_id": trace_id},
                input=input,
            ) as span,
            propagate_attributes(session_id=session_id or trace_id, tags=tags),
        ):
            yield span
