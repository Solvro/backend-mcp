import logging

import pytest
from chat_app.mcp_gateway import KnowledgeGraphGateway
from chat_app.settings import ChatSettings
from common.context import trace_id_var
from common.observability import (
    get_langfuse,
    new_trace_id,
    shutdown_langfuse,
    start_turn_trace,
)

# Needs a real ml-mcp server and Langfuse keys; `just test-e2e` and CI deselect `live`.
pytestmark = [pytest.mark.e2e, pytest.mark.live]

_PROBE_INIT_TIMEOUT = 2.0


async def test_trace_id_propagates_across_backend_mcp_and_langfuse(caplog) -> None:
    settings = ChatSettings()

    langfuse = get_langfuse(settings)
    if langfuse is None:
        pytest.skip("Langfuse keys not set — cannot verify span propagation")

    gateway = KnowledgeGraphGateway(
        settings.mcp_server_url,
        timeout=settings.mcp_timeout_seconds,
        init_timeout=_PROBE_INIT_TIMEOUT,
        max_retries=0,
        retry_base_delay=0.0,
        retry_max_delay=0.0,
    )
    try:
        await gateway._ensure_client()
    except Exception as exc:  # noqa: BLE001
        await gateway.aclose()
        shutdown_langfuse()
        pytest.skip(f"ml-mcp not reachable at {settings.mcp_server_url}: {exc!r}")

    trace_id = new_trace_id()
    logger = logging.getLogger("chat_app.e2e")

    try:
        with caplog.at_level(logging.INFO):
            with start_turn_trace(trace_id, name="e2e-chat-turn", settings=settings) as span:
                assert span is not None
                assert langfuse.get_current_trace_id() == trace_id

                assert trace_id_var.get() == trace_id
                logger.info("processing chat turn")

                answer = await gateway.query("gdzie jest sala 101?", trace_id)
                assert isinstance(answer, str)

                message_metadata = {"source": "mcp", "trace_id": trace_id}

        turn_logs = [r for r in caplog.records if r.name == "chat_app.e2e"]
        assert turn_logs and trace_id_var.get() is None  # context restored after block
        assert message_metadata["trace_id"] == trace_id

        langfuse.flush()
        trace_url = langfuse.get_trace_url(trace_id=trace_id)
        assert trace_url and trace_id in trace_url
    finally:
        await gateway.aclose()
        shutdown_langfuse()
