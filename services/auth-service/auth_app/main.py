from common.exceptions_handlers import register_exception_handlers
from common.health import build_health_router
from common.logging import setup_logging
from common.metrics import setup_metrics
from common.middleware import setup_middleware
from fastapi import FastAPI

from auth_app.api.auth import router as auth_router
from auth_app.health import build_dependencies
from auth_app.settings import get_settings

settings = get_settings()

setup_logging(service_name="auth-service", log_level=settings.log_level)

app = FastAPI(title="ml-mcp-backend · auth-service", version="0.1.0")

setup_middleware(app, settings)

register_exception_handlers(app)

setup_metrics(app, settings)

app.include_router(
    build_health_router(
        service_name="auth-service",
        dependencies_provider=lambda: build_dependencies(settings),
    )
)

app.include_router(auth_router)
