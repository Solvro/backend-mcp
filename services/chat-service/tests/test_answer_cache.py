import json
import time

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


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]], *, error: Exception | None = None) -> None:
        self.vectors = vectors
        self.error = error
        self.calls: list[str] = []

    async def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return self.vectors[text]


def _cache(
    redis: FakeRedis,
    *,
    ttl: int = 3600,
    embedder: FakeEmbedder | None = None,
    threshold: float = 0.92,
    max_entries: int = 100,
) -> AnswerCache:
    return AnswerCache(
        redis,
        ttl_seconds=ttl,
        settings=ChatSettings(),
        embedder=embedder,
        similarity_threshold=threshold,
        similarity_max_entries=max_entries,
    )


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
    await cache.store("q", "a")


async def test_distinct_questions_do_not_collide() -> None:
    redis = FakeRedis()
    cache = _cache(redis)

    await cache.store("Pytanie A?", "A")
    await cache.store("Pytanie B?", "B")

    assert await cache.lookup("Pytanie A?") == "A"
    assert await cache.lookup("Pytanie B?") == "B"


def test_similarity_enabled_reflects_embedder() -> None:
    assert _cache(FakeRedis()).similarity_enabled is False
    assert _cache(FakeRedis(), embedder=FakeEmbedder({})).similarity_enabled is True


async def test_similarity_hit_serves_reworded_question() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder(
        {
            "Gdzie jest sala 301?": [1.0, 0.0, 0.0],
            "W którym budynku jest sala 301?": [0.99, 0.01, 0.0],  # near-parallel
        }
    )
    cache = _cache(redis, embedder=embedder)

    await cache.store("Gdzie jest sala 301?", "Sala 301 jest w C-16.")

    assert await cache.lookup("W którym budynku jest sala 301?") == "Sala 301 jest w C-16."


async def test_similarity_miss_below_threshold() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder(
        {"Q1": [1.0, 0.0, 0.0], "Q2": [0.0, 1.0, 0.0]}  # orthogonal -> score 0
    )
    cache = _cache(redis, embedder=embedder)

    await cache.store("Q1", "A1")

    assert await cache.lookup("Q2") is None


async def test_exact_hit_skips_embedding() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder({"Q1": [1.0, 0.0, 0.0]})
    cache = _cache(redis, embedder=embedder)

    await cache.store("Q1", "A1")
    assert await cache.lookup("Q1") == "A1"

    assert embedder.calls == ["Q1"]


async def test_similarity_dimension_mismatch_is_miss() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder({"Q1": [1.0, 0.0, 0.0], "Q2": [1.0, 0.0]})  # different dims
    cache = _cache(redis, embedder=embedder)

    await cache.store("Q1", "A1")

    assert await cache.lookup("Q2") is None


async def test_similarity_embedding_error_fails_open() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder({}, error=RuntimeError("embed down"))
    cache = _cache(redis, embedder=embedder)

    await cache.store("Q1", "A1")
    assert await cache.lookup("Q2") is None
    assert await cache.lookup("Q1") == "A1"


async def test_similarity_index_is_capped() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder(
        {"Q1": [1.0, 0.0], "Q2": [0.0, 1.0], "Q3": [1.0, 1.0]}
    )
    cache = _cache(redis, embedder=embedder, max_entries=2)

    await cache.store("Q1", "A1")
    await cache.store("Q2", "A2")
    await cache.store("Q3", "A3")

    entries = json.loads(redis.store[cache._sim_index_key()])
    assert [e["a"] for e in entries] == ["A2", "A3"]


async def test_similarity_expired_entries_ignored() -> None:
    redis = FakeRedis()
    embedder = FakeEmbedder({"Q2": [1.0, 0.0, 0.0]})
    cache = _cache(redis, embedder=embedder)

    redis.store[cache._sim_index_key()] = json.dumps(
        [{"v": [1.0, 0.0, 0.0], "a": "stale", "exp": time.time() - 1}]
    )

    assert await cache.lookup("Q2") is None
