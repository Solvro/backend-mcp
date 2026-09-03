import hashlib
import logging

from common.redis import Namespace, get_redis, make_key
from redis.asyncio import Redis

from chat_app.settings import ChatSettings

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v1"
_ANSWER_SUBSPACE = "answer"


def normalize_query(question: str) -> str:
    return " ".join(question.split()).casefold()


class AnswerCache:
    def __init__(self, redis: Redis, *, ttl_seconds: int, settings: ChatSettings) -> None:
        self._redis = redis
        self._ttl = max(1, ttl_seconds)
        self._settings = settings

    def _key(self, question: str) -> str:
        seed = f"{_CACHE_VERSION}:{normalize_query(question)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return make_key(Namespace.CACHE, _ANSWER_SUBSPACE, digest, settings=self._settings)

    async def lookup(self, question: str) -> str | None:
        try:
            return await self._redis.get(self._key(question))
        except Exception:
            logger.warning("Answer cache lookup failed -> treating as miss", exc_info=True)
            return None

    async def store(self, question: str, answer: str) -> None:
        try:
            await self._redis.set(self._key(question), answer, ex=self._ttl)
        except Exception:
            logger.warning("Answer cache store failed -> answer not cached", exc_info=True)


def build_answer_cache(settings: ChatSettings, *, redis: Redis | None = None) -> AnswerCache | None:
    if not settings.answer_cache_enabled:
        return None
    if redis is None:
        if not settings.redis_url:
            logger.info("Answer cache enabled but REDIS_URL is unset -> cache disabled")
            return None
        redis = get_redis(settings)
    return AnswerCache(redis, ttl_seconds=settings.answer_cache_ttl_seconds, settings=settings)
