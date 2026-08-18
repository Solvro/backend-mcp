import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.exceptions_handlers import register_exception_handlers
from common.logging import setup_logging
from common.middleware import setup_middleware
from common.mongo import close_mongo_client, create_indexes
from common.rate_limit import rate_limit
from fastapi import Depends, FastAPI

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
    yield
    await close_mongo_client()


app = FastAPI(title="ml-mcp-backend - chat-service", version="0.1.0", lifespan=lifespan)

setup_middleware(app, get_settings())

register_exception_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}


# Placeholder endpoint until real implementation
@app.post("/api/chat", dependencies=[Depends(rate_limit("chat:message", settings=settings))])
async def chat() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}
