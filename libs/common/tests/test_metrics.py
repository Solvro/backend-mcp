import pytest
from common.metrics import setup_metrics
from common.settings import CommonSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

pytestmark = pytest.mark.unit


def _app(**settings_overrides) -> tuple[FastAPI, TestClient]:
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict:
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("kaboom")

    settings = CommonSettings(**settings_overrides)
    setup_metrics(app, settings, registry=CollectorRegistry())
    return app, TestClient(app, raise_server_exceptions=False)


def test_metrics_endpoint_exposes_prometheus_format() -> None:
    _, client = _app()
    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "http_request_duration_seconds" in resp.text


def test_disabled_metrics_returns_none_and_no_endpoint() -> None:
    app = FastAPI()
    result = setup_metrics(app, CommonSettings(metrics_enabled=False))

    assert result is None
    assert "/metrics" not in {route.path for route in app.routes}


def test_request_counter_increments() -> None:
    _, client = _app()

    client.get("/ping")
    client.get("/ping")

    body = client.get("/metrics").text
    line = _find(body, "http_requests_total{", 'handler="/ping"', 'status="200"')
    assert line is not None
    assert float(line.rsplit(" ", 1)[1]) >= 2.0


def test_error_requests_are_counted_with_5xx_status() -> None:
    _, client = _app()

    client.get("/boom")

    body = client.get("/metrics").text
    line = _find(body, "http_requests_total{", 'handler="/boom"', 'status="500"')
    assert line is not None
    assert float(line.rsplit(" ", 1)[1]) >= 1.0


def test_path_params_are_templated_not_leaked() -> None:
    _, client = _app()

    client.get("/items/secret-user-value")

    body = client.get("/metrics").text
    assert 'handler="/items/{item_id}"' in body
    assert "secret-user-value" not in body


def test_metrics_endpoint_excluded_from_its_own_metrics() -> None:
    _, client = _app()

    client.get("/metrics")
    body = client.get("/metrics").text

    assert 'handler="/metrics"' not in body


def _find(body: str, *needles: str) -> str | None:
    for raw in body.splitlines():
        if raw.startswith("#"):
            continue
        if all(n in raw for n in needles):
            return raw
    return None
