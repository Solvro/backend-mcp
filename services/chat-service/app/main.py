from fastapi import FastAPI

app = FastAPI(title="ml-mcp-backend · chat-service", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}
