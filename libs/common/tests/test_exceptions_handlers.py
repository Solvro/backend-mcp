import json

import pytest
from common.errors import (
    AuthError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
    ValidationError,
)
from common.exceptions_handlers import register_exception_handlers
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/error/auth")
    def _auth():
        raise AuthError()

    @app.get("/error/rate")
    def _rate():
        raise RateLimitedError()

    @app.get("/error/upstream")
    def _upstream():
        raise UpstreamError()

    @app.get("/error/validation")
    def _validation():
        raise ValidationError()

    @app.get("/error/notfound")
    def _notfound():
        raise NotFoundError()

    @app.get("/unknown")
    def _unknown():
        raise RuntimeError("Szpont")

    @app.get("/fastapi-validate")
    def _fastapi_validate(q: int):
        return {"q": q}

    @app.get("/http-exc")
    def _http_exc():
        raise HTTPException(status_code=403, detail="forbidden")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "endpoint, expected_status, expected_title, expected_detail",
    [
        (
            "/error/auth",
            401,
            "Authentication Error",
            "Authentication failed or credentials were not provided.",
        ),
        (
            "/error/rate",
            429,
            "Too Many Requests",
            "Rate limit exceeded.",
        ),
        (
            "/error/upstream",
            502,
            "Upstream Service Error",
            "Error communicating with upstream service.",
        ),
        (
            "/error/validation",
            422,
            "Validation Error",
            "The request parameters or body are invalid.",
        ),
        (
            "/error/notfound",
            404,
            "Resource Not Found",
            "The requested resource could not be found.",
        ),
    ],
)
@pytest.mark.unit
def test_app_errors_return_correct_rfc7807_format(
    client: TestClient,
    endpoint: str,
    expected_status: int,
    expected_title: str,
    expected_detail: str,
):
    r = client.get(endpoint)
    assert r.status_code == expected_status
    body = r.json()

    for key in ("type", "title", "status", "detail", "request_id"):
        assert key in body

    assert body["status"] == expected_status
    assert body["title"] == expected_title
    assert body["detail"] == expected_detail


@pytest.mark.unit
def test_unknown_exception_does_not_leak_stacktrace(client: TestClient):
    r = client.get("/unknown")
    assert r.status_code == 500
    body = r.json()
    assert body["title"] == "Internal Server Error"
    assert "traceback" not in json.dumps(body).lower()


@pytest.mark.unit
def test_fastapi_validation_error_returns_rfc7807_list_detail(client: TestClient):
    r = client.get("/fastapi-validate", params={"q": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    for key in ("type", "title", "status", "detail", "request_id"):
        assert key in body
    assert isinstance(body["detail"], list)


@pytest.mark.unit
def test_http_exception_mapped_to_rfc7807(client: TestClient):
    r = client.get("/http-exc")
    assert r.status_code == 403
    body = r.json()
    assert body["title"] == "HTTP Exception"
    assert body["type"] == "about:blank"
    assert body["detail"] == "forbidden"
