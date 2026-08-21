import pytest
from chat_app.mcp_gateway import KnowledgeGraphGateway
from chat_app.settings import ChatSettings
from common.errors import UpstreamError

pytestmark = pytest.mark.e2e

_PROBE_INIT_TIMEOUT = 2.0


def _make_gateway(settings: ChatSettings, **overrides) -> KnowledgeGraphGateway:
    params = dict(
        timeout=settings.mcp_timeout_seconds,
        init_timeout=_PROBE_INIT_TIMEOUT,
        max_retries=0,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    params.update(overrides)
    return KnowledgeGraphGateway(settings.mcp_server_url, **params)


@pytest.fixture
async def gateway():
    settings = ChatSettings()
    gw = _make_gateway(settings)
    try:
        await gw._ensure_client()
    except Exception as exc:  # noqa: BLE001
        await gw.aclose()
        pytest.skip(f"ml-mcp not reachable at {settings.mcp_server_url}: {exc!r}")
    try:
        yield gw
    finally:
        await gw.aclose()


async def test_real_query_returns_text(gateway) -> None:
    text = await gateway.query("gdzie jest sala 101?", trace_id="e2e-query")

    assert isinstance(text, str)
    assert text.strip()


async def test_real_connection_is_reused(gateway) -> None:
    await gateway.query("pierwsze pytanie", trace_id="e2e-reuse-1")
    client_after_first = gateway._client

    await gateway.query("drugie pytanie", trace_id="e2e-reuse-2")

    assert gateway._client is client_after_first
    assert gateway._client.is_connected()


async def test_real_query_times_out_cleanly(gateway):
    settings = ChatSettings()
    fast = _make_gateway(settings, timeout=0.001)
    try:
        with pytest.raises(UpstreamError) as excinfo:
            await fast.query("cokolwiek", trace_id="e2e-timeout")
        assert isinstance(excinfo.value.__cause__, TimeoutError)
    finally:
        await fast.aclose()
