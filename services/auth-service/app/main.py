from common.context import RequestContext
from common.exceptions_handlers import register_exception_handlers
from common.logging import setup_logging
from fastapi import FastAPI

from app.settings import get_settings

setup_logging(service_name="auth-service", log_level=get_settings().log_level)

app = FastAPI(title="ml-mcp-backend · auth-service", version="0.1.0")

app.add_middleware(RequestContext)

register_exception_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "auth-service"}
