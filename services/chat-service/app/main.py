import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.context import RequestContext
from common.exceptions_handlers import register_exception_handlers
from common.logging import setup_logging
from common.mongo import close_mongo_client, create_indexes
from fastapi import FastAPI

from app.settings import get_settings

setup_logging(service_name="chat-service", log_level=get_settings().log_level)

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

app.add_middleware(RequestContext)

register_exception_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}
