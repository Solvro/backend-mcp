import pytest
from chat_app import health as chat_health
from chat_app.settings import ChatSettings
from common.health import build_health_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _settings(**overrides) -> ChatSettings:
    base = dict(
        mongo_uri="mongodb://mongo:27017",
        redis_url="redis://redis:6379",
        mcp_server_url="http://mcp:8005/mcp",
    )
    base.update(overrides)
    return ChatSettings(**base)


def _patch(monkeypatch, *, mongo: bool, redis: bool, mcp: bool) -> None:
    async def ok() -> bool:
        return True

    def failing(name):
        async def _fail(*_args, **_kwargs) -> bool:
            raise ConnectionError(f"{name} down")

        return _fail

    monkeypatch.setattr(chat_health, "check_mongo", ok if mongo else failing("mongo"))
    monkeypatch.setattr(chat_health, "check_redis", ok if redis else failing("redis"))

    async def mcp_probe(*_args, **_kwargs) -> bool:
        if not mcp:
            raise ConnectionError("mcp down")
        return True

    monkeypatch.setattr(chat_health, "check_mcp", mcp_probe)


def _client(settings: ChatSettings) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_health_router(
            service_name="chat-service",
            dependencies_provider=lambda: chat_health.build_dependencies(settings),
        )
    )
    return TestClient(app)


def test_build_dependencies_includes_configured_stores() -> None:
    deps = chat_health.build_dependencies(_settings())
    by_name = {d.name: d for d in deps}

    assert set(by_name) == {"mongo", "redis", "mcp"}
    assert by_name["mongo"].required is True
    assert by_name["redis"].required is True
    assert by_name["mcp"].required is False  # MCP down is degraded, not a hard fail


def test_build_dependencies_skips_unconfigured_stores() -> None:
    deps = chat_health.build_dependencies(_settings(mongo_uri="", mcp_server_url=""))
    assert {d.name for d in deps} == {"redis"}


def test_ready_ok_when_all_up(monkeypatch) -> None:
    _patch(monkeypatch, mongo=True, redis=True, mcp=True)
    resp = _client(_settings()).get("/health/ready")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ready_degraded_when_mcp_down(monkeypatch) -> None:
    _patch(monkeypatch, mongo=True, redis=True, mcp=False)
    resp = _client(_settings()).get("/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["mcp"]["status"] == "down"


def test_ready_unhealthy_when_mongo_down(monkeypatch) -> None:
    _patch(monkeypatch, mongo=False, redis=True, mcp=True)
    resp = _client(_settings()).get("/health/ready")

    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"


def test_live_ignores_dependencies(monkeypatch) -> None:
    _patch(monkeypatch, mongo=False, redis=False, mcp=False)
    resp = _client(_settings()).get("/health/live")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
