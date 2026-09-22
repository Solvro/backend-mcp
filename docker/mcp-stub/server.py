from fastapi import FastAPI
from fastmcp import FastMCP

mcp = FastMCP("integration-stub")


@mcp.tool
def knowledge_graph_tool(
    user_input: str,
    trace_id: str | None = None,
    session_id: str | None = None,
) -> str:
    return f"Integration MCP answer for: {user_input}"


mcp_app = mcp.http_app(path="/")
app = FastAPI(lifespan=mcp_app.lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/mcp", mcp_app)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)
