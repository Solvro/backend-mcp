import asyncio

import pytest
from common.health import (
    Dependency,
    HealthStatus,
    build_health_router,
    run_health_checks,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


async def _up() -> bool:
    return True


async def _down() -> bool:
    raise ConnectionError("refused")


async def _hang() -> bool:
    await asyncio.sleep(10)
    return True


def _dep(name: str, probe, *, required: bool = True, timeout: float = 1.0) -> Dependency:
    return Dependency(name=name, probe=probe, required=required, timeout=timeout)


async def test_all_up_is_ok() -> None:
    report = await run_health_checks([_dep("mongo", _up), _dep("redis", _up)])

    assert report.status is HealthStatus.OK
    assert report.http_status == 200
    assert report.to_dict()["checks"]["mongo"]["status"] == "up"


async def test_required_down_is_unhealthy() -> None:
    report = await run_health_checks([_dep("mongo", _up), _dep("redis", _down)])

    assert report.status is HealthStatus.UNHEALTHY
    assert report.http_status == 503
    checks = report.to_dict()["checks"]
    assert checks["redis"]["status"] == "down"
    assert checks["redis"]["error"]


async def test_optional_down_is_degraded_not_failed() -> None:
    report = await run_health_checks(
        [_dep("mongo", _up), _dep("mcp", _down, required=False)]
    )

    assert report.status is HealthStatus.DEGRADED
    assert report.http_status == 200  # still serving
    assert report.to_dict()["checks"]["mcp"]["required"] is False


async def test_required_down_wins_over_optional_down() -> None:
    report = await run_health_checks(
        [_dep("mongo", _down), _dep("mcp", _down, required=False)]
    )

    assert report.status is HealthStatus.UNHEALTHY


async def test_probe_timeout_counts_as_down() -> None:
    report = await run_health_checks([_dep("mongo", _hang, timeout=0.01)])

    assert report.status is HealthStatus.UNHEALTHY
    assert report.to_dict()["checks"]["mongo"]["status"] == "down"


def _client(deps: list[Dependency], name: str = "svc") -> TestClient:
    app = FastAPI()
    app.include_router(
        build_health_router(service_name=name, dependencies_provider=lambda: deps)
    )
    return TestClient(app)


def test_live_is_always_ok_without_probing() -> None:
    # a required store is down, but liveness must not probe it
    client = _client([_dep("mongo", _down)])
    resp = client.get("/health/live")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "svc"}


def test_ready_returns_503_when_required_down() -> None:
    client = _client([_dep("mongo", _down), _dep("redis", _up)])
    resp = client.get("/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["service"] == "svc"
    assert body["checks"]["mongo"]["status"] == "down"


def test_ready_returns_200_degraded_when_optional_down() -> None:
    client = _client([_dep("mongo", _up), _dep("mcp", _down, required=False)])
    resp = client.get("/health/ready")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_health_returns_full_report() -> None:
    client = _client([_dep("mongo", _up), _dep("redis", _up)])
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_dependencies_provider_called_per_request() -> None:
    calls = {"n": 0}

    def provider() -> list[Dependency]:
        calls["n"] += 1
        return [_dep("mongo", _up)]

    app = FastAPI()
    app.include_router(
        build_health_router(service_name="svc", dependencies_provider=provider)
    )
    client = TestClient(app)

    client.get("/health/ready")
    client.get("/health/ready")

    assert calls["n"] == 2
