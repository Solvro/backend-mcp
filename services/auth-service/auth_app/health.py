"""Dependency wiring for the auth-service health endpoints.

Postgres and Redis are required: if either is down the service is not ready.
"""

from common.db import check_database
from common.health import Dependency
from common.redis import check_redis

from auth_app.settings import AuthSettings


def build_dependencies(settings: AuthSettings) -> list[Dependency]:
    timeout = settings.health_probe_timeout_seconds
    dependencies: list[Dependency] = []

    if settings.database_url:
        dependencies.append(
            Dependency(
                name="postgres", probe=check_database, required=True, timeout=timeout
            )
        )
    if settings.redis_url:
        dependencies.append(
            Dependency(name="redis", probe=check_redis, required=True, timeout=timeout)
        )

    return dependencies
