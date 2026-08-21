import asyncio

import httpx
import pytest
from chat_app.mcp_gateway import (
    NO_KNOWLEDGE_SENTINEL,
    CircuitBreaker,
    CircuitState,
    KnowledgeGraphGateway,
    is_no_knowledge,
)
from common.errors import ServiceUnavailableError, UpstreamError
from fastmcp import FastMCP

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def _ok(user_input, trace_id):
    return "ok"


def make_gateway(transport, **overrides) -> KnowledgeGraphGateway:
    params = dict(
        timeout=5.0,
        init_timeout=5.0,
        max_retries=2,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    params.update(overrides)
    return KnowledgeGraphGateway(transport, **params)


def stub_server(handler) -> FastMCP:
    server = FastMCP("stub-mcp")

    @server.tool
    async def knowledge_graph_tool(user_input: str, trace_id: str | None = None) -> str:
        return await handler(user_input, trace_id)

    return server


async def test_returns_tool_text_on_success() -> None:
    async def handler(user_input, trace_id):
        return f"graph context for: {user_input}"

    async with make_gateway(stub_server(handler)) as gw:
        assert await gw.query("gdzie jest sala 101?") == (
            "graph context for: gdzie jest sala 101?"
        )


async def test_forwards_user_input_and_trace_id() -> None:
    async def handler(user_input, trace_id):
        return f"{user_input}|{trace_id}"

    async with make_gateway(stub_server(handler)) as gw:
        assert await gw.query("hi", "trace-1") == "hi|trace-1"


async def test_reuses_connection_across_calls() -> None:
    async def handler(user_input, trace_id):
        return "ok"

    async with make_gateway(stub_server(handler)) as gw:
        await gw.query("a")
        client_after_first = gw._client
        await gw.query("b")

        assert gw._client is client_after_first
        assert gw._client.is_connected()


async def test_times_out_cleanly() -> None:
    async def handler(user_input, trace_id):
        await asyncio.sleep(1)
        return "too late"

    gw = make_gateway(stub_server(handler), timeout=0.05, max_retries=0)
    with pytest.raises(UpstreamError) as excinfo:
        await gw.query("q")
    assert isinstance(excinfo.value.__cause__, TimeoutError)
    await gw.aclose()


async def test_retries_transient_then_succeeds(monkeypatch) -> None:
    async def handler(user_input, trace_id):
        return "ok"

    gw = make_gateway(stub_server(handler), max_retries=2)
    calls = {"n": 0}

    async def flaky(user_input, trace_id):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return "recovered"

    monkeypatch.setattr(gw, "_call_tool_once", flaky)

    assert await gw.query("q") == "recovered"
    assert calls["n"] == 3
    await gw.aclose()


async def test_gives_up_after_max_retries(monkeypatch) -> None:
    async def handler(user_input, trace_id):
        return "ok"

    gw = make_gateway(stub_server(handler), max_retries=1)
    calls = {"n": 0}

    async def always_fails(user_input, trace_id):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr(gw, "_call_tool_once", always_fails)

    with pytest.raises(UpstreamError):
        await gw.query("q")
    assert calls["n"] == 2
    await gw.aclose()


async def test_tool_error_is_not_retried() -> None:
    calls = {"n": 0}

    async def handler(user_input, trace_id):
        calls["n"] += 1
        raise ValueError("tool blew up")

    gw = make_gateway(stub_server(handler), max_retries=3)
    with pytest.raises(UpstreamError):
        await gw.query("q")
    assert calls["n"] == 1
    await gw.aclose()


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://mcp/mcp")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError(),
        ConnectionError(),
        httpx.ConnectError("x"),
        httpx.ReadTimeout("x"),
        _status_error(503),
        _status_error(429),
    ],
)
def test_is_transient_true(exc) -> None:
    assert KnowledgeGraphGateway._is_transient(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("nope"),
        _status_error(400),
        _status_error(404),
    ],
)
def test_is_transient_false(exc) -> None:
    assert KnowledgeGraphGateway._is_transient(exc) is False


def test_is_transient_follows_cause_chain() -> None:
    try:
        raise RuntimeError("wrapper") from httpx.ConnectError("root cause")
    except RuntimeError as exc:
        assert KnowledgeGraphGateway._is_transient(exc) is True


async def test_sentinel_is_returned_verbatim_and_not_a_failure() -> None:
    async def handler(user_input, trace_id):
        return NO_KNOWLEDGE_SENTINEL

    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30.0)
    gw = make_gateway(stub_server(handler), breaker=breaker)

    assert await gw.query("pytanie nie na temat") == NO_KNOWLEDGE_SENTINEL
    assert breaker.state is CircuitState.CLOSED
    await gw.aclose()


@pytest.mark.parametrize(
    "text",
    [NO_KNOWLEDGE_SENTINEL, "  " + NO_KNOWLEDGE_SENTINEL + " ", "[]", "{}", ""],
)
def test_is_no_knowledge_true(text) -> None:
    assert is_no_knowledge(text) is True


@pytest.mark.parametrize("text", ['[{"n": 1}]', "Sala 101 jest w budynku C-13.", "{...}"])
def test_is_no_knowledge_false(text) -> None:
    assert is_no_knowledge(text) is False


async def test_circuit_opens_and_fails_fast(monkeypatch) -> None:
    breaker = CircuitBreaker(failure_threshold=2, reset_timeout=30.0)
    gw = make_gateway(stub_server(_ok), breaker=breaker, max_retries=0)

    calls = {"n": 0}

    async def always_fails(user_input, trace_id):
        calls["n"] += 1
        raise httpx.ConnectError("mcp down")

    monkeypatch.setattr(gw, "_call_tool_once", always_fails)

    for _ in range(2):
        with pytest.raises(ServiceUnavailableError):
            await gw.query("q")

    assert breaker.state is CircuitState.OPEN
    assert calls["n"] == 2

    with pytest.raises(ServiceUnavailableError) as excinfo:
        await gw.query("q")
    assert calls["n"] == 2  # unchanged — no hang, no call
    assert excinfo.value.status_code == 503
    assert excinfo.value.headers["Retry-After"]
    await gw.aclose()


async def test_circuit_recovers_after_cooldown(monkeypatch) -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, time_func=clock)
    gw = make_gateway(stub_server(_ok), breaker=breaker, max_retries=0)

    state = {"fail": True}

    async def flaky(user_input, trace_id):
        if state["fail"]:
            raise httpx.ConnectError("mcp down")
        return "recovered"

    monkeypatch.setattr(gw, "_call_tool_once", flaky)

    with pytest.raises(ServiceUnavailableError):
        await gw.query("q")  # trips the breaker
    assert breaker.state is CircuitState.OPEN

    with pytest.raises(ServiceUnavailableError):
        await gw.query("q")  # still open -> fast fail

    clock.advance(30.0)  # cooldown elapsed
    state["fail"] = False

    assert await gw.query("q") == "recovered"  # half-open probe succeeds
    assert breaker.state is CircuitState.CLOSED
    await gw.aclose()
