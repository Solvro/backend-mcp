import asyncio
import logging
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from chat_app.settings import ChatSettings

if TYPE_CHECKING:
    from google.genai import Client as GoogleClient
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingProvider(StrEnum):
    OPENAI = "openai"
    GEMINI = "gemini"


_AUTO_ORDER: tuple[EmbeddingProvider, ...] = (
    EmbeddingProvider.OPENAI,
    EmbeddingProvider.GEMINI,
)


@runtime_checkable
class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    async def aclose(self) -> None: ...


def _chunk(texts: list[str], size: int) -> list[list[str]]:
    return [texts[i : i + size] for i in range(0, len(texts), size)]


class OpenAIEmbedder:
    def __init__(
        self,
        client: "AsyncOpenAI",
        model: str,
        *,
        max_batch_size: int,
        max_concurrent_batches: int,
    ) -> None:
        self._client = client
        self._model = model
        self._max_batch = max(1, max_batch_size)
        self._sem = asyncio.Semaphore(max(1, max_concurrent_batches))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []

        async def run(chunk: list[str]) -> list[list[float]]:
            async with self._sem:
                resp = await self._client.embeddings.create(model=self._model, input=chunk)
            ordered = sorted(resp.data, key=lambda d: d.index)
            return [d.embedding for d in ordered]

        results = await asyncio.gather(*(run(c) for c in _chunk(items, self._max_batch)))
        return [vector for chunk_result in results for vector in chunk_result]

    async def aclose(self) -> None:
        await self._client.close()


class GeminiEmbedder:
    def __init__(
        self,
        client: "GoogleClient",
        model: str,
        *,
        max_batch_size: int,
        max_concurrent_batches: int,
    ) -> None:
        self._client = client
        self._model = model
        self._max_batch = max(1, max_batch_size)
        self._sem = asyncio.Semaphore(max(1, max_concurrent_batches))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []

        async def run(chunk: list[str]) -> list[list[float]]:
            async with self._sem:
                resp = await self._client.aio.models.embed_content(
                    model=self._model, contents=chunk
                )
            return [list(e.values or []) for e in resp.embeddings or []]

        results = await asyncio.gather(*(run(c) for c in _chunk(items, self._max_batch)))
        return [vector for chunk_result in results for vector in chunk_result]

    async def aclose(self) -> None:
        return None


def _build_model(provider: EmbeddingProvider, settings: ChatSettings) -> Embedder | None:
    common = {
        "max_batch_size": settings.embedding_batch_max_size,
        "max_concurrent_batches": settings.embedding_max_concurrent_batches,
    }
    if provider is EmbeddingProvider.OPENAI and settings.openai_api_key:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        return OpenAIEmbedder(client, settings.embedding_openai_model, **common)
    if provider is EmbeddingProvider.GEMINI and settings.google_api_key:
        from google.genai import Client

        client = Client(api_key=settings.google_api_key)
        return GeminiEmbedder(client, settings.embedding_gemini_model, **common)
    return None


def select_embedding_model(settings: ChatSettings) -> Embedder | None:
    configured = settings.embedding_provider.strip().lower()
    if configured:
        try:
            provider = EmbeddingProvider(configured)
        except ValueError:
            logger.warning(
                "Unknown embedding_provider %r; falling back to auto-selection", configured
            )
        else:
            model = _build_model(provider, settings)
            if model is not None:
                logger.info("Embedding provider selected: %s", provider.value)
                return model
            logger.warning(
                "Configured embedding provider %r has no API key; trying auto-selection",
                configured,
            )

    for provider in _AUTO_ORDER:
        model = _build_model(provider, settings)
        if model is not None:
            logger.info("Embedding provider selected (auto): %s", provider.value)
            return model

    logger.warning("No LLM API key configured (OpenAI/Gemini): embeddings unavailable")
    return None
