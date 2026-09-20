from common.health import Dependency
from common.mongo import check_mongo
from common.redis import check_redis

from chat_app.mcp_gateway import KnowledgeGraphGateway, check_mcp
from chat_app.settings import ChatSettings


def build_dependencies(
    settings: ChatSettings, gateway: KnowledgeGraphGateway | None = None
) -> list[Dependency]:
    """Readiness probes. Pass the app's gateway so the MCP probe pings the live session
    instead of opening a new one per probe."""
    timeout = settings.health_probe_timeout_seconds
    dependencies: list[Dependency] = []

    if settings.mongo_uri:
        dependencies.append(
            Dependency(name="mongo", probe=check_mongo, required=True, timeout=timeout)
        )
    if settings.redis_url:
        dependencies.append(
            Dependency(name="redis", probe=check_redis, required=True, timeout=timeout)
        )
    if settings.mcp_server_url:
        dependencies.append(
            Dependency(
                name="mcp",
                probe=(
                    (lambda: gateway.ping(timeout=timeout))
                    if gateway is not None
                    else lambda: check_mcp(
                        settings.mcp_server_url, init_timeout=timeout, timeout=timeout
                    )
                ),
                required=False,
                timeout=timeout,
            )
        )

    return dependencies
