from fastapi import FastAPI
from common.exceptions_handlers import register_exception_handlers

app = FastAPI(title="ml-mcp-backend · chat-service", version="0.1.0")

register_exception_handlers(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}
