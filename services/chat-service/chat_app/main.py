import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.auth import optional_auth
from common.exceptions_handlers import register_exception_handlers
from common.health import build_health_router
from common.logging import setup_logging
from common.metrics import setup_metrics
from common.middleware import setup_middleware
from common.mongo import close_mongo_client, create_indexes
from common.observability import get_langfuse, shutdown_langfuse
from common.rate_limit import daily_quota, rate_limit
from fastapi import Depends, FastAPI

from chat_app.api.sessions import build_sessions_router
from chat_app.health import build_dependencies
from chat_app.settings import get_settings

settings = get_settings()

setup_logging(service_name="chat-service", log_level=settings.log_level)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.mongo_uri:
        try:
            await create_indexes(settings)
        except Exception:
            logger.warning("Mongo index bootstrap failed", exc_info=True)
    else:
        logger.info("MONGO_URI not set — skipping Mongo index bootstrap")
    get_langfuse(settings)
    yield
    shutdown_langfuse()
    await close_mongo_client()


app = FastAPI(title="ml-mcp-backend - chat-service", version="0.1.0", lifespan=lifespan)

setup_middleware(app, get_settings())

register_exception_handlers(app)

setup_metrics(app, get_settings())

app.include_router(
    build_health_router(
        service_name="chat-service",
        dependencies_provider=lambda: build_dependencies(get_settings()),
    )
)

app.include_router(build_sessions_router(settings))


# Placeholder endpoint until real implementation (SES-2).
# optional_auth runs first so the daily quota can pick the anonymous vs authenticated allowance.
@app.post(
    "/api/chat",
    dependencies=[
        Depends(optional_auth(settings=settings)),
        Depends(rate_limit("chat:message", settings=settings)),
        Depends(
            daily_quota(
                "chat:message",
                settings=settings,
                anonymous_limit=settings.chat_daily_quota_anonymous,
                authenticated_limit=settings.chat_daily_quota_authenticated,
            )
        ),
    ],
)
async def chat() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}
