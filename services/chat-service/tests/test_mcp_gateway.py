import asyncio

import httpx
import pytest
from chat_app.mcp_gateway import KnowledgeGraphGateway
from common.errors import UpstreamError
from fastmcp import FastMCP

pytestmark = pytest.mark.unit


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
