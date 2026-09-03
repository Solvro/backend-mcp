import hashlib
import sys
import types

import pytest
from common import observability
from common.context import trace_id_var
from common.observability import (
    get_langfuse,
    is_langfuse_enabled,
    new_trace_id,
    shutdown_langfuse,
    start_span,
    start_turn_trace,
    use_trace_id,
)
from common.settings import CommonSettings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_singleton():
    observability._reset_for_tests()
    yield
    observability._reset_for_tests()


def _settings(**overrides) -> CommonSettings:
    base = dict(langfuse_secret_key="", langfuse_public_key="", langfuse_host="http://lf")
    base.update(overrides)
    return CommonSettings(**base)


class FakeLangfuse:
    instances: list["FakeLangfuse"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.shutdown_called = False
        FakeLangfuse.instances.append(self)

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def fake_langfuse(monkeypatch):
    FakeLangfuse.instances = []
    module = types.ModuleType("langfuse")
    module.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return FakeLangfuse


def test_disabled_without_keys() -> None:
    assert get_langfuse(_settings()) is None
    assert is_langfuse_enabled(_settings()) is False


def test_disabled_with_only_public_key() -> None:
    assert get_langfuse(_settings(langfuse_public_key="pk")) is None


def test_disabled_with_only_secret_key() -> None:
    assert get_langfuse(_settings(langfuse_secret_key="sk")) is None


def test_enabled_with_both_keys(fake_langfuse):
    client = get_langfuse(
        _settings(langfuse_secret_key="sk", langfuse_public_key="pk", langfuse_host="http://lf")
    )

    assert client is not None
    assert client.kwargs == {
        "public_key": "pk",
        "secret_key": "sk",
        "host": "http://lf",
    }


def test_single_init_per_process(fake_langfuse) -> None:
    settings = _settings(langfuse_secret_key="sk", langfuse_public_key="pk")

    first = get_langfuse(settings)
    second = get_langfuse(settings)

    assert first is second
    assert len(fake_langfuse.instances) == 1


def test_missing_package_degrades_to_disabled(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)

    assert get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk")) is None


def test_init_failure_degrades_to_disabled(monkeypatch) -> None:
    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("cannot reach langfuse")

    module = types.ModuleType("langfuse")
    module.Langfuse = Boom
    monkeypatch.setitem(sys.modules, "langfuse", module)

    assert get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk")) is None


def test_shutdown_flushes_and_resets(fake_langfuse):
    client = get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk"))

    shutdown_langfuse()

    assert client.shutdown_called is True
    assert get_langfuse(_settings()) is None


def test_shutdown_is_safe_when_disabled() -> None:
    assert get_langfuse(_settings()) is None
    shutdown_langfuse()


def test_new_trace_id_is_32_lowercase_hex() -> None:
    trace_id = new_trace_id()

    assert len(trace_id) == 32
    assert trace_id == trace_id.lower()
    int(trace_id, 16)


def test_new_trace_id_seed_is_deterministic() -> None:
    assert new_trace_id(seed="session-abc") == new_trace_id(seed="session-abc")


def test_new_trace_id_seed_matches_langfuse_algorithm() -> None:
    expected = hashlib.sha256(b"abc").hexdigest()[:32]
    assert new_trace_id(seed="abc") == expected


def test_new_trace_id_random_ids_differ() -> None:
    assert new_trace_id() != new_trace_id()


def test_new_trace_id_fallback_without_langfuse(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)

    trace_id = new_trace_id(seed="abc")

    assert trace_id == hashlib.sha256(b"abc").hexdigest()[:32]


def test_use_trace_id_binds_and_restores_log_context() -> None:
    assert trace_id_var.get() is None

    with use_trace_id("cafe1234") as bound:
        assert bound == "cafe1234"
        assert trace_id_var.get() == "cafe1234"

    assert trace_id_var.get() is None


def test_start_turn_trace_disabled_yields_none_but_still_binds_logs() -> None:
    with start_turn_trace("deadbeef", settings=_settings()) as span:
        assert span is None
        assert trace_id_var.get() == "deadbeef"

    assert trace_id_var.get() is None


class _FakeSpan:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _TracingLangfuse:
    last: "_TracingLangfuse | None" = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.observations: list[_FakeSpan] = []
        _TracingLangfuse.last = self

    def start_as_current_observation(self, **kwargs):
        span = _FakeSpan(**kwargs)
        self.observations.append(span)
        return span


@pytest.fixture
def fake_tracing(monkeypatch):
    captured: dict = {}

    def fake_propagate_attributes(**kwargs):
        captured["propagate"] = kwargs

        class _CM:
            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        return _CM()

    module = types.ModuleType("langfuse")
    module.Langfuse = _TracingLangfuse
    module.propagate_attributes = fake_propagate_attributes
    monkeypatch.setitem(sys.modules, "langfuse", module)
    _TracingLangfuse.last = None
    return captured


def test_start_turn_trace_opens_span_on_trace_id_and_session(fake_tracing) -> None:
    settings = _settings(langfuse_secret_key="sk", langfuse_public_key="pk")

    with start_turn_trace("abc123", name="chat-turn", settings=settings) as span:
        assert span is not None
        assert trace_id_var.get() == "abc123"

    client = _TracingLangfuse.last
    obs = client.observations[0]
    assert obs.kwargs["trace_context"] == {"trace_id": "abc123"}
    assert obs.kwargs["name"] == "chat-turn"
    assert fake_tracing["propagate"]["session_id"] == "abc123"


def test_start_turn_trace_uses_explicit_session_id(fake_tracing) -> None:
    settings = _settings(langfuse_secret_key="sk", langfuse_public_key="pk")

    with start_turn_trace("abc123", session_id="sess-9", settings=settings):
        pass

    assert fake_tracing["propagate"]["session_id"] == "sess-9"


def test_start_span_disabled_yields_none() -> None:
    with start_span("mcp.knowledge_graph", input="q", settings=_settings()) as span:
        assert span is None


def test_start_span_opens_child_observation(fake_tracing) -> None:
    settings = _settings(langfuse_secret_key="sk", langfuse_public_key="pk")

    with start_span("answer-llm", as_type="generation", input="pytanie", settings=settings) as span:
        assert span is not None

    obs = _TracingLangfuse.last.observations[0]
    assert obs.kwargs["name"] == "answer-llm"
    assert obs.kwargs["as_type"] == "generation"
    assert obs.kwargs["input"] == "pytanie"
