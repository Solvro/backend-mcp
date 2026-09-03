import pytest
from chat_app.answer import build_answer_cache, normalize_query
from chat_app.answer.cache import AnswerCache
from chat_app.settings import ChatSettings

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, str, int | None]] = []
        self.error = error

    async def get(self, key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        if self.error is not None:
            raise self.error
        self.store[key] = value
        self.sets.append((key, value, ex))


def _cache(redis: FakeRedis, *, ttl: int = 3600) -> AnswerCache:
    return AnswerCache(redis, ttl_seconds=ttl, settings=ChatSettings())


def test_normalize_collapses_whitespace_and_case() -> None:
    assert normalize_query("  Gdzie   jest\tSALA 301? ") == "gdzie jest sala 301?"


def test_disabled_by_settings_returns_none() -> None:
    settings = ChatSettings(answer_cache_enabled=False, redis_url="redis://x")
    assert build_answer_cache(settings) is None


def test_enabled_without_redis_url_returns_none() -> None:
    settings = ChatSettings(answer_cache_enabled=True, redis_url="")
    assert build_answer_cache(settings) is None


def test_build_uses_injected_redis() -> None:
    settings = ChatSettings(answer_cache_enabled=True, redis_url="")
    cache = build_answer_cache(settings, redis=FakeRedis())
    assert isinstance(cache, AnswerCache)


async def test_store_then_lookup_round_trips() -> None:
    redis = FakeRedis()
    cache = _cache(redis, ttl=1234)

    assert await cache.lookup("Gdzie jest sala 301?") is None
    await cache.store("Gdzie jest sala 301?", "Sala 301 jest w C-16.")

    assert await cache.lookup("Gdzie jest sala 301?") == "Sala 301 jest w C-16."
    assert redis.sets[0][2] == 1234


async def test_lookup_is_normalization_insensitive() -> None:
    redis = FakeRedis()
    cache = _cache(redis)

    await cache.store("Gdzie jest sala 301?", "answer")

    assert await cache.lookup("  gdzie   JEST sala 301? ") == "answer"


async def test_lookup_failure_degrades_to_miss() -> None:
    cache = _cache(FakeRedis(error=RuntimeError("redis down")))
    assert await cache.lookup("q") is None


async def test_store_failure_is_swallowed() -> None:
    cache = _cache(FakeRedis(error=RuntimeError("redis down")))
    # Must not raise even though the underlying redis errors.
    await cache.store("q", "a")


async def test_distinct_questions_do_not_collide() -> None:
    redis = FakeRedis()
    cache = _cache(redis)

    await cache.store("Pytanie A?", "A")
    await cache.store("Pytanie B?", "B")

    assert await cache.lookup("Pytanie A?") == "A"
    assert await cache.lookup("Pytanie B?") == "B"
