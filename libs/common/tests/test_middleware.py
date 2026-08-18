import pytest
from common.middleware import RequestContext, setup_middleware
from common.settings import CommonSettings
from fastapi import FastAPI, Request
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


@pytest.mark.unit
def test_request_id_is_propagated(settings):
    app = FastAPI()
    setup_middleware(app, settings)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)

    request_id = "test-request-id"

    response = client.get("/test", headers={"X-Request-ID": request_id},)

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id


@pytest.mark.unit
def test_security_headers_present(settings):
    app = FastAPI()
    setup_middleware(app, settings)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == (
        "strict-origin-when-cross-origin"
    )
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


@pytest.mark.unit
def test_security_headers_can_be_disabled():
    settings = CommonSettings(security_headers_enabled=False)
    app = FastAPI()
    setup_middleware(app, settings)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    assert "x-frame-options" not in response.headers


@pytest.mark.unit
def test_body_within_limit_passes():
    settings = CommonSettings(max_request_body_size=1000)
    app = FastAPI()
    setup_middleware(app, settings)

    @app.post("/test")
    async def test_endpoint(payload: dict):
        return payload

    client = TestClient(app)
    response = client.post("/test", json={"key": "value"})

    assert response.status_code == 200
    assert response.json() == {"key": "value"}
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.unit
def test_oversized_body_returns_413():
    settings = CommonSettings(max_request_body_size=100)
    app = FastAPI()
    setup_middleware(app, settings)

    @app.post("/test")
    async def test_endpoint(payload: dict):
        return payload

    client = TestClient(app)
    response = client.post("/test", content=b"x" * 200)

    assert response.status_code == 413
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.unit
def test_oversized_chunked_body_returns_413():
    settings = CommonSettings(max_request_body_size=100)
    app = FastAPI()
    setup_middleware(app, settings)

    @app.post("/test")
    async def test_endpoint(request: Request):
        body = await request.body()
        return {"len": len(body)}

    def stream():
        for _ in range(10):
            yield b"x" * 50

    client = TestClient(app)
    response = client.post("/test", content=stream())

    assert response.status_code == 413
