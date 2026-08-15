import pytest
from common.middleware import RequestContext, setup_middleware
from common.settings import CommonSettings
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def settings():
    return CommonSettings(
        cors_allow_origins=["http://localhost:3000"],
        cors_allow_credentials=True,
        cors_allow_methods=["GET", "POST"],
        cors_allow_headers=["*"],
        cors_expose_headers=["X-Request-ID", "X-Process-Time"],
    )


@pytest.mark.unit
def test_request_context_middleware():
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    app.add_middleware(RequestContext)

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert "x-process-time" in response.headers
    assert len(response.headers["x-request-id"]) > 0


@pytest.mark.unit
def test_cors_middleware(settings):
    app = FastAPI()

    setup_middleware(app, settings)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)

    response = client.get("/test", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers.get(
        "access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.unit
def test_cors_middleware_with_unauthorized_origin(settings):
    app = FastAPI()
    setup_middleware(app, settings)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)

    response = client.get("/test", headers={
        "Origin": "http://unauthorized"
        })

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") is None
