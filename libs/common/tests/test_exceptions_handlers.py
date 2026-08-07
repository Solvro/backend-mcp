import json

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from common.errors import AuthError
from common.exceptions_handlers import register_exception_handlers


def _create_app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-error")
    def app_error():
        raise AuthError("bad auth")

    @app.get("/unknown")
    def unknown():
        raise RuntimeError("boom")

    @app.get("/validate")
    def validate(q: int):
        return {"q": q}

    @app.get("/http-exc")
    def http_exc():
        raise HTTPException(status_code=403, detail="forbidden")

    return app


def test_app_error_response_fields_and_content():
    client = TestClient(_create_app())
    r = client.get("/app-error")
    assert r.status_code == 401
    body = r.json()
    # required RFC7807 fields
    for key in ("type", "title", "status", "detail", "request_id"):
        assert key in body
    assert body["status"] == 401
    assert body["detail"] == "bad auth"


def test_unknown_exception_does_not_leak_stacktrace():
    client = TestClient(_create_app())
    r = client.get("/unknown")
    assert r.status_code == 500
    body = r.json()
    assert body["title"] == "Internal Server Error"
    # generic detail message (no raw exception text or stack trace)
    assert body["detail"] == "An unexpected internal server error occurred."
    # ensure no obvious traceback fields are returned
    assert "traceback" not in json.dumps(body).lower()


def test_validation_error_returns_rfc7807_list_detail():
    client = TestClient(_create_app())
    r = client.get("/validate", params={"q": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    for key in ("type", "title", "status", "detail", "request_id"):
        assert key in body
    assert isinstance(body["detail"], list)


def test_http_exception_mapped_to_rfc7807():
    client = TestClient(_create_app())
    r = client.get("/http-exc")
    assert r.status_code == 403
    body = r.json()
    assert body["title"] == "HTTP Exception"
    assert body["type"] == "about:blank"
    assert body["detail"] == "forbidden"
