import asyncio
import logging
from dataclasses import dataclass

from chat_app.embeddings.provider import Embedder, select_embedding_model
from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)


@dataclass
class _Pending:
    text: str
    future: asyncio.Future[list[float]]


class BatchingEmbedder:
    def __init__(
        self,
        model: Embedder,
        *,
        max_batch_size: int = 128,
        window_ms: float = 10.0,
        max_concurrent_batches: int = 4,
    ) -> None:
        self._model = model
        self._max_batch = max(1, max_batch_size)
        self._window = max(0.0, window_ms) / 1000.0
        self._sem = asyncio.Semaphore(max(1, max_concurrent_batches))
        self._queue: list[_Pending] = []
        self._flush_handle: asyncio.Handle | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._closed = False

    async def embed_one(self, text: str) -> list[float]:
        if self._closed:
            raise RuntimeError("BatchingEmbedder is closed")

        loop = asyncio.get_running_loop()
        pending = _Pending(text, loop.create_future())
        self._queue.append(pending)

        if len(self._queue) >= self._max_batch:
            self._cancel_timer()
            self._flush()
        elif self._flush_handle is None:
            self._flush_handle = loop.call_later(self._window, self._flush)

        return await pending.future

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return await self._model.embed(texts)

    def _cancel_timer(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    def _flush(self) -> None:
        self._flush_handle = None
        if not self._queue:
            return

        batch, self._queue = self._queue, []
        task = asyncio.ensure_future(self._run_batch(batch))
        self._inflight.add(task)
        task.add_done_callback(self._inflight.discard)

    async def _run_batch(self, batch: list[_Pending]) -> None:
        try:
            async with self._sem:
                vectors = await self._model.embed([p.text for p in batch])
        except Exception as exc:  # noqa: BLE001 - propagated to every waiter
            logger.warning("Embedding batch of %d failed", len(batch), exc_info=True)
            for pending in batch:
                if not pending.future.done():
                    pending.future.set_exception(exc)
            return

        if len(vectors) != len(batch):
            err = RuntimeError(
                f"Embedding provider returned {len(vectors)} vectors for {len(batch)} inputs"
            )
            for pending in batch:
                if not pending.future.done():
                    pending.future.set_exception(err)
            return

        for pending, vector in zip(batch, vectors):
            if not pending.future.done():
                pending.future.set_result(vector)

    async def aclose(self) -> None:
        self._closed = True
        self._cancel_timer()
        while self._queue:
            self._flush()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        await self._model.aclose()


def build_embedder(
    settings: ChatSettings, *, model: Embedder | None = None
) -> BatchingEmbedder | None:
    if not settings.embedding_enabled:
        return None
    model = model or select_embedding_model(settings)
    if model is None:
        logger.warning("Embeddings enabled but no API key configured -> embeddings disabled")
        return None
    return BatchingEmbedder(
        model,
        max_batch_size=settings.embedding_batch_max_size,
        window_ms=settings.embedding_batch_window_ms,
        max_concurrent_batches=settings.embedding_max_concurrent_batches,
    )
