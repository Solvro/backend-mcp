import pytest
from common.context import user_id_var
from common.exceptions_handlers import register_exception_handlers
from common.rate_limit import _client_identity, _rate_limit_headers, rate_limit
from common.redis import RateLimitResult
from common.settings import CommonSettings
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request


def _request(headers: list[tuple[bytes, bytes]] | None = None, client=("1.2.3.4", 0)) -> Request:
    return Request({"type": "http", "headers": headers or [], "client": client})


@pytest.mark.unit
def test_identity_prefers_authenticated_user():
    token = user_id_var.set("42")
    try:
        assert _client_identity(_request()) == "user:42"
    finally:
        user_id_var.reset(token)


@pytest.mark.unit
def test_identity_uses_forwarded_ip_when_anonymous():
    req = _request(headers=[(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")])
    assert _client_identity(req) == "ip:9.9.9.9"


@pytest.mark.unit
def test_identity_falls_back_to_socket_peer():
    assert _client_identity(_request()) == "ip:1.2.3.4"


@pytest.mark.unit
def test_identity_unknown_without_client():
    assert _client_identity(_request(client=None)) == "ip:unknown"


@pytest.mark.unit
def test_headers_include_retry_after_only_when_asked():
    result = RateLimitResult(
        allowed=False, limit=5, remaining=0, reset_seconds=12, retry_after=12
    )
    allowed = _rate_limit_headers(result, include_retry_after=False)
    assert allowed == {"RateLimit-Limit": "5", "RateLimit-Remaining": "0", "RateLimit-Reset": "12"}
    assert "Retry-After" not in allowed

    blocked = _rate_limit_headers(result, include_retry_after=True)
    assert blocked["Retry-After"] == "12"


def _app_with_stub(monkeypatch, settings: CommonSettings, scope: str, *, allowed: bool) -> FastAPI:
    async def fake_check(scope_, identifier, *, limit, window_seconds, settings=None):
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=limit - 1 if allowed else 0,
            reset_seconds=window_seconds,
            retry_after=0 if allowed else window_seconds,
        )

    monkeypatch.setattr("common.rate_limit.check_rate_limit", fake_check)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/limited", dependencies=[Depends(rate_limit(scope, settings=settings))])
    def _limited():
        return {"ok": True}

    return app


@pytest.mark.unit
def test_allowed_request_carries_ratelimit_headers(monkeypatch):
    settings = CommonSettings(rate_limit=7, rate_limit_window_seconds=30)
    client = TestClient(_app_with_stub(monkeypatch, settings, "chat", allowed=True))

    r = client.get("/limited")
    assert r.status_code == 200
    assert r.headers["RateLimit-Limit"] == "7"
    assert r.headers["RateLimit-Remaining"] == "6"
    assert r.headers["RateLimit-Reset"] == "30"
    assert "Retry-After" not in r.headers


@pytest.mark.unit
def test_blocked_request_returns_429_with_retry_after(monkeypatch):
    settings = CommonSettings(rate_limit=3, rate_limit_window_seconds=60)
    client = TestClient(
        _app_with_stub(monkeypatch, settings, "chat", allowed=False),
        raise_server_exceptions=False,
    )

    r = client.get("/limited")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert r.headers["RateLimit-Limit"] == "3"
    body = r.json()
    assert body["title"] == "Too Many Requests"
    assert body["status"] == 429


@pytest.mark.unit
def test_login_and_chat_get_distinct_limits(monkeypatch):
    login_settings = CommonSettings(rate_limit=5)
    chat_settings = CommonSettings(rate_limit=30)

    login = TestClient(_app_with_stub(monkeypatch, login_settings, "login", allowed=True))
    assert login.get("/limited").headers["RateLimit-Limit"] == "5"

    chat = TestClient(_app_with_stub(monkeypatch, chat_settings, "chat", allowed=True))
    assert chat.get("/limited").headers["RateLimit-Limit"] == "30"


@pytest.mark.unit
def test_route_override_beats_service_default(monkeypatch):
    settings = CommonSettings(rate_limit=30)

    async def fake_check(scope_, identifier, *, limit, window_seconds, settings=None):
        return RateLimitResult(
            allowed=True, limit=limit, remaining=limit, reset_seconds=window_seconds, retry_after=0
        )

    monkeypatch.setattr("common.rate_limit.check_rate_limit", fake_check)

    app = FastAPI()

    @app.get("/tight", dependencies=[Depends(rate_limit("burst", settings=settings, limit=2))])
    def _tight():
        return {"ok": True}

    assert TestClient(app).get("/tight").headers["RateLimit-Limit"] == "2"


@pytest.mark.unit
def test_disabled_limiter_is_a_noop(monkeypatch):
    settings = CommonSettings(rate_limit_enabled=False)

    async def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("limiter should be bypassed when disabled")

    monkeypatch.setattr("common.rate_limit.check_rate_limit", boom)

    app = FastAPI()

    @app.get("/limited", dependencies=[Depends(rate_limit("chat", settings=settings))])
    def _limited():
        return {"ok": True}

    r = TestClient(app).get("/limited")
    assert r.status_code == 200
    assert "RateLimit-Limit" not in r.headers
