import hashlib
import json
import logging
import time
from typing import Any, Protocol, runtime_checkable

from common.redis import Namespace, get_redis, make_key
from redis.asyncio import Redis

from chat_app.embeddings import most_similar
from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v1"
_ANSWER_SUBSPACE = "answer"
_SIM_INDEX_KEY = "simidx"


@runtime_checkable
class QueryEmbedder(Protocol):
    async def embed_one(self, text: str) -> list[float]: ...


def normalize_query(question: str) -> str:
    return " ".join(question.split()).casefold()


class AnswerCache:
    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        settings: ChatSettings,
        embedder: QueryEmbedder | None = None,
        similarity_threshold: float = 0.92,
        similarity_max_entries: int = 100,
    ) -> None:
        self._redis = redis
        self._ttl = max(1, ttl_seconds)
        self._settings = settings
        self._embedder = embedder
        self._threshold = similarity_threshold
        self._max_entries = max(1, similarity_max_entries)

    @property
    def similarity_enabled(self) -> bool:
        return self._embedder is not None

    def _key(self, question: str) -> str:
        seed = f"{_CACHE_VERSION}:{normalize_query(question)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return make_key(Namespace.CACHE, _ANSWER_SUBSPACE, digest, settings=self._settings)

    def _sim_index_key(self) -> str:
        return make_key(
            Namespace.CACHE, _ANSWER_SUBSPACE, _SIM_INDEX_KEY, settings=self._settings
        )

    async def lookup(self, question: str) -> str | None:
        try:
            exact = await self._redis.get(self._key(question))
        except Exception:
            logger.warning("Answer cache lookup failed -> treating as miss", exc_info=True)
            return None
        if exact is not None:
            return exact
        return await self._similarity_lookup(question)

    async def store(self, question: str, answer: str) -> None:
        try:
            await self._redis.set(self._key(question), answer, ex=self._ttl)
        except Exception:
            logger.warning("Answer cache store failed -> answer not cached", exc_info=True)
            return
        await self._similarity_store(question, answer)

    async def _similarity_lookup(self, question: str) -> str | None:
        if self._embedder is None:
            return None
        try:
            vector = await self._embedder.embed_one(question)
            entries = await self._load_index()
            candidates = [e["v"] for e in entries if len(e["v"]) == len(vector)]
            if not candidates:
                return None
            index, score = most_similar(vector, candidates)
            if index >= 0 and score >= self._threshold:
                logger.debug("Answer cache similarity hit (score=%.3f)", score)
                return entries[index]["a"]
        except Exception:
            logger.warning(
                "Answer cache similarity lookup failed -> treating as miss", exc_info=True
            )
        return None

    async def _similarity_store(self, question: str, answer: str) -> None:
        if self._embedder is None:
            return
        try:
            vector = await self._embedder.embed_one(question)
            entries = await self._load_index()
            now = time.time()
            entries = [e for e in entries if e.get("exp", 0) > now]
            entries.append({"v": vector, "a": answer, "exp": now + self._ttl})
            entries = entries[-self._max_entries :]
            await self._redis.set(
                self._sim_index_key(), json.dumps(entries), ex=self._ttl
            )
        except Exception:
            logger.warning(
                "Answer cache similarity store failed -> entry not indexed", exc_info=True
            )

    async def _load_index(self) -> list[dict[str, Any]]:
        raw = await self._redis.get(self._sim_index_key())
        if not raw:
            return []
        now = time.time()
        entries = json.loads(raw)
        return [e for e in entries if e.get("exp", 0) > now]


def build_answer_cache(
    settings: ChatSettings,
    *,
    redis: Redis | None = None,
    embedder: QueryEmbedder | None = None,
) -> AnswerCache | None:
    if not settings.answer_cache_enabled:
        return None
    if redis is None:
        if not settings.redis_url:
            logger.info("Answer cache enabled but REDIS_URL is unset -> cache disabled")
            return None
        redis = get_redis(settings)
    return AnswerCache(
        redis,
        ttl_seconds=settings.answer_cache_ttl_seconds,
        settings=settings,
        embedder=embedder,
        similarity_threshold=settings.answer_cache_similarity_threshold,
        similarity_max_entries=settings.answer_cache_similarity_max_entries,
    )
