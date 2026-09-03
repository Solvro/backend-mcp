import asyncio

import pytest
from chat_app.embeddings import (
    BatchingEmbedder,
    GeminiEmbedder,
    OpenAIEmbedder,
    build_embedder,
    cosine_similarities,
    cosine_similarity,
    most_similar,
    select_embedding_model,
)
from chat_app.embeddings.provider import EmbeddingProvider
from chat_app.settings import ChatSettings

pytestmark = pytest.mark.unit


def _settings(**overrides) -> ChatSettings:
    base = {"openai_api_key": "", "google_api_key": "", "embedding_enabled": True}
    base.update(overrides)
    return ChatSettings(**base)


def test_cosine_identical_is_one() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one() -> None:
    assert cosine_similarity([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_is_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0], [1.0, 2.0])


def test_cosine_similarities_batched() -> None:
    query = [1.0, 0.0]
    candidates = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
    sims = cosine_similarities(query, candidates)
    assert sims.tolist() == pytest.approx([1.0, 0.0, -1.0])


def test_cosine_similarities_empty_candidates() -> None:
    assert cosine_similarities([1.0, 2.0], []).tolist() == []


def test_cosine_similarities_zero_row_is_zero_not_nan() -> None:
    sims = cosine_similarities([1.0, 1.0], [[0.0, 0.0], [1.0, 1.0]])
    assert sims.tolist() == pytest.approx([0.0, 1.0])


def test_cosine_similarities_dimension_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cosine_similarities([1.0, 2.0], [[1.0, 2.0, 3.0]])


def test_most_similar_picks_best() -> None:
    query = [1.0, 0.0]
    candidates = [[0.2, 0.9], [0.95, 0.05], [-1.0, 0.0]]
    index, score = most_similar(query, candidates)
    assert index == 1
    assert score == pytest.approx(cosine_similarity(query, candidates[1]))


def test_most_similar_empty_returns_sentinel() -> None:
    assert most_similar([1.0, 2.0], []) == (-1, 0.0)


class _OpenAIItem:
    def __init__(self, embedding: list[float], index: int) -> None:
        self.embedding = embedding
        self.index = index


class _OpenAIResp:
    def __init__(self, data: list[_OpenAIItem]) -> None:
        self.data = data


class FakeOpenAIClient:
    def __init__(self, *, reverse: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.reverse = reverse
        self.closed = False
        client = self

        class _Embeddings:
            async def create(self, *, model: str, input: list[str]) -> _OpenAIResp:
                client.calls.append(list(input))
                data = [_OpenAIItem([float(len(t)), float(i)], i) for i, t in enumerate(input)]
                if client.reverse:
                    data = list(reversed(data))
                return _OpenAIResp(data)

        self.embeddings = _Embeddings()

    async def close(self) -> None:
        self.closed = True


async def test_openai_empty_input_returns_empty() -> None:
    model = OpenAIEmbedder(
        FakeOpenAIClient(), "m", max_batch_size=2, max_concurrent_batches=2
    )
    assert await model.embed([]) == []


async def test_openai_chunks_into_sub_batches_preserving_order() -> None:
    client = FakeOpenAIClient()
    model = OpenAIEmbedder(client, "m", max_batch_size=2, max_concurrent_batches=4)

    result = await model.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert client.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert [vec[0] for vec in result] == [1.0, 2.0, 3.0, 4.0, 5.0]


async def test_openai_realigns_out_of_order_response() -> None:
    client = FakeOpenAIClient(reverse=True)
    model = OpenAIEmbedder(client, "m", max_batch_size=10, max_concurrent_batches=1)

    result = await model.embed(["a", "bb", "ccc"])

    assert [vec[1] for vec in result] == [0.0, 1.0, 2.0]


async def test_openai_aclose_closes_client() -> None:
    client = FakeOpenAIClient()
    model = OpenAIEmbedder(client, "m", max_batch_size=2, max_concurrent_batches=2)
    await model.aclose()
    assert client.closed is True


class _GeminiEmb:
    def __init__(self, values: list[float]) -> None:
        self.values = values


class _GeminiResp:
    def __init__(self, embeddings: list[_GeminiEmb]) -> None:
        self.embeddings = embeddings


class FakeGoogleClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        client = self

        class _Models:
            async def embed_content(self, *, model: str, contents: list[str]) -> _GeminiResp:
                client.calls.append(list(contents))
                return _GeminiResp([_GeminiEmb([float(len(c))]) for c in contents])

        class _Aio:
            models = _Models()

        self.aio = _Aio()


async def test_gemini_chunks_and_preserves_order() -> None:
    client = FakeGoogleClient()
    model = GeminiEmbedder(client, "m", max_batch_size=2, max_concurrent_batches=4)

    result = await model.embed(["a", "bb", "ccc"])

    assert client.calls == [["a", "bb"], ["ccc"]]
    assert [vec[0] for vec in result] == [1.0, 2.0, 3.0]


def test_select_none_without_keys() -> None:
    assert select_embedding_model(_settings()) is None


def test_select_openai_preferred() -> None:
    model = select_embedding_model(_settings(openai_api_key="ok", google_api_key="gk"))
    assert isinstance(model, OpenAIEmbedder)


def test_select_gemini_when_only_google_key() -> None:
    model = select_embedding_model(_settings(google_api_key="gk"))
    assert isinstance(model, GeminiEmbedder)


def test_select_explicit_provider_overrides_priority() -> None:
    model = select_embedding_model(
        _settings(embedding_provider="gemini", openai_api_key="ok", google_api_key="gk")
    )
    assert isinstance(model, GeminiEmbedder)


def test_select_unknown_provider_falls_back_to_auto() -> None:
    model = select_embedding_model(_settings(embedding_provider="bogus", openai_api_key="ok"))
    assert isinstance(model, OpenAIEmbedder)


def test_embedding_provider_enum_values() -> None:
    assert {p.value for p in EmbeddingProvider} == {"openai", "gemini"}


def test_build_disabled_returns_none() -> None:
    assert build_embedder(_settings(embedding_enabled=False, openai_api_key="ok")) is None


def test_build_enabled_without_keys_returns_none() -> None:
    assert build_embedder(_settings()) is None


def test_build_with_injected_model() -> None:
    embedder = build_embedder(_settings(), model=RecordingModel())
    assert isinstance(embedder, BatchingEmbedder)


class RecordingModel:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.batches: list[list[str]] = []
        self.error = error
        self.closed = False

    async def embed(self, texts) -> list[list[float]]:
        items = list(texts)
        self.batches.append(items)
        if self.error is not None:
            raise self.error
        return [[float(len(t))] for t in items]

    async def aclose(self) -> None:
        self.closed = True


async def test_coalesces_concurrent_calls_into_one_batch() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=10, window_ms=50)

    results = await asyncio.gather(
        embedder.embed_one("a"),
        embedder.embed_one("bb"),
        embedder.embed_one("ccc"),
    )

    assert model.batches == [["a", "bb", "ccc"]]  # a single provider call
    assert results == [[1.0], [2.0], [3.0]]


async def test_flushes_immediately_when_batch_is_full() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=2, window_ms=100_000)

    results = await asyncio.gather(
        embedder.embed_one("a"),
        embedder.embed_one("bb"),
        embedder.embed_one("ccc"),
        embedder.embed_one("dddd"),
    )

    assert model.batches == [["a", "bb"], ["ccc", "dddd"]]
    assert results == [[1.0], [2.0], [3.0], [4.0]]


async def test_flushes_after_window_timeout() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=100, window_ms=10)

    assert await embedder.embed_one("x") == [1.0]
    assert model.batches == [["x"]]


async def test_provider_error_propagates_to_all_waiters() -> None:
    model = RecordingModel(error=RuntimeError("boom"))
    embedder = BatchingEmbedder(model, max_batch_size=10, window_ms=10)

    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.gather(embedder.embed_one("a"), embedder.embed_one("bb"))


async def test_vector_count_mismatch_raises() -> None:
    class ShortModel:
        async def embed(self, texts) -> list[list[float]]:
            return []

        async def aclose(self) -> None:
            pass

    embedder = BatchingEmbedder(ShortModel(), max_batch_size=10, window_ms=10)

    with pytest.raises(RuntimeError, match="returned 0 vectors"):
        await embedder.embed_one("x")


async def test_embed_many_bypasses_coalescing() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=10, window_ms=100_000)

    result = await embedder.embed_many(["a", "bb"])

    assert result == [[1.0], [2.0]]
    assert model.batches == [["a", "bb"]]


async def test_aclose_flushes_pending_and_closes_model() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=10, window_ms=100_000)

    task = asyncio.ensure_future(embedder.embed_one("a"))
    await asyncio.sleep(0)

    await embedder.aclose()

    assert await task == [1.0]
    assert model.batches == [["a"]]
    assert model.closed is True


async def test_embed_one_after_close_raises() -> None:
    model = RecordingModel()
    embedder = BatchingEmbedder(model, max_batch_size=10, window_ms=10)
    await embedder.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await embedder.embed_one("x")
