import asyncio
import logging
import math
import random
from typing import Any

import httpx
from common.errors import ServiceUnavailableError, UpstreamError
from fastmcp import Client

from chat_app.mcp_gateway.circuit_breaker import CircuitBreaker
from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)

TOOL_NAME = "knowledge_graph_tool"

NO_KNOWLEDGE_SENTINEL = "W bazie danych nie ma informacji"
_EMPTY_RESULTS = frozenset({"", "[]", "{}"})


def is_no_knowledge(text: str) -> bool:
    stripped = text.strip()
    return stripped == NO_KNOWLEDGE_SENTINEL or stripped in _EMPTY_RESULTS

_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)
_TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})


class KnowledgeGraphGateway:
    def __init__(
        self,
        transport: Any,
        *,
        timeout: float,
        init_timeout: float,
        max_retries: int,
        retry_base_delay: float,
        retry_max_delay: float,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._transport = transport
        self._timeout = timeout
        self._init_timeout = init_timeout
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._breaker = breaker

        self._client: Client | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: ChatSettings) -> "KnowledgeGraphGateway":
        breaker = None
        if settings.mcp_breaker_enabled:
            breaker = CircuitBreaker(
                failure_threshold=settings.mcp_breaker_failure_threshold,
                reset_timeout=settings.mcp_breaker_reset_timeout_seconds,
            )
        return cls(
            settings.mcp_server_url,
            timeout=settings.mcp_timeout_seconds,
            init_timeout=settings.mcp_init_timeout_seconds,
            max_retries=settings.mcp_max_retries,
            retry_base_delay=settings.mcp_retry_base_delay,
            retry_max_delay=settings.mcp_retry_max_delay,
            breaker=breaker,
        )

    async def query(self, user_input: str, trace_id: str | None = None) -> str:
        if self._breaker is not None and not self._breaker.allow():
            retry_after = max(1, math.ceil(self._breaker.retry_after()))
            logger.warning("MCP circuit open, failing fast (retry_after=%ds)", retry_after)
            raise ServiceUnavailableError(headers={"Retry-After": str(retry_after)})

        try:
            result = await self._run_with_retry(user_input, trace_id)
        except UpstreamError:
            if self._breaker is not None:
                self._breaker.record_failure()
            raise
        if self._breaker is not None:
            self._breaker.record_success()
        return result

    async def _run_with_retry(self, user_input: str, trace_id: str | None) -> str:
        attempt = 0
        while True:
            try:
                return await self._call_tool_once(user_input, trace_id)
            except Exception as exc:  # noqa: BLE001 - classified below
                transient = self._is_transient(exc)
                if transient and attempt < self._max_retries:
                    await self._drop_client()
                    delay = self._backoff(attempt)
                    logger.warning(
                        "MCP call failed (transient), retrying "
                        "(attempt=%d/%d, delay=%.3fs)",
                        attempt + 1,
                        self._max_retries,
                        delay,
                        exc_info=True,
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                await self._drop_client()
                if transient:
                    raise ServiceUnavailableError(
                        "The knowledge graph service is unavailable."
                    ) from exc
                raise UpstreamError(
                    "The knowledge graph service returned an error."
                ) from exc

    async def _call_tool_once(self, user_input: str, trace_id: str | None) -> str:
        client = await self._ensure_client()
        async with asyncio.timeout(self._timeout):
            result = await client.call_tool(
                TOOL_NAME,
                {"user_input": user_input, "trace_id": trace_id},
            )
        return self._join_text(result)

    async def _ensure_client(self) -> Client:
        async with self._lock:
            if self._client is None or not self._client.is_connected():
                if self._client is not None:
                    await _safe_close(self._client)
                client = Client(self._transport, init_timeout=self._init_timeout)
                await client.__aenter__()
                self._client = client
            return self._client

    async def _drop_client(self) -> None:
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            await _safe_close(client)

    def _backoff(self, attempt: int) -> float:
        capped = min(self._retry_max_delay, self._retry_base_delay * (2**attempt))
        return capped + random.uniform(0, self._retry_base_delay)

    @staticmethod
    def _is_transient(exc: BaseException) -> bool:
        cursor: BaseException | None = exc
        while cursor is not None:
            if isinstance(cursor, _TRANSIENT_EXCEPTIONS):
                return True
            if isinstance(cursor, httpx.HTTPStatusError):
                return cursor.response.status_code in _TRANSIENT_STATUS_CODES
            cursor = cursor.__cause__
        return False

    @staticmethod
    def _join_text(result: Any) -> str:
        return "\n".join(
            block.text for block in result.content if hasattr(block, "text")
        )

    async def aclose(self) -> None:
        await self._drop_client()

    async def __aenter__(self) -> "KnowledgeGraphGateway":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


async def check_mcp(
    transport: Any,
    *,
    init_timeout: float = 5.0,
    timeout: float = 5.0,
) -> bool:
    client = Client(transport, init_timeout=init_timeout)
    async with asyncio.timeout(timeout):
        async with client:
            await client.ping()
    return True


async def _safe_close(client: Client) -> None:
    try:
        await client.__aexit__(None, None, None)
    except Exception:  # noqa: BLE001 - teardown must not mask the real error
        logger.debug("Error while closing MCP client", exc_info=True)
