from common.exceptions_handlers import register_exception_handlers
from common.health import build_health_router
from common.logging import setup_logging
from common.middleware import setup_middleware
from common.rate_limit import rate_limit
from fastapi import Depends, FastAPI

from auth_app.health import build_dependencies
from auth_app.settings import get_settings

settings = get_settings()

setup_logging(service_name="auth-service", log_level=settings.log_level)

app = FastAPI(title="ml-mcp-backend · auth-service", version="0.1.0")

setup_middleware(app, get_settings())

register_exception_handlers(app)

app.include_router(
    build_health_router(
        service_name="auth-service",
        dependencies_provider=lambda: build_dependencies(get_settings()),
    )
)


# Placeholder endpoint until real implementation
@app.post("/auth/login", dependencies=[Depends(rate_limit("auth:login", settings=settings))])
async def login() -> dict[str, str]:
    return {"status": "ok", "service": "auth-service"}
