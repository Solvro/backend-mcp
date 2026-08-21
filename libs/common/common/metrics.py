import logging

from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from prometheus_fastapi_instrumentator import Instrumentator

from common.settings import CommonSettings

logger = logging.getLogger(__name__)


def setup_metrics(
    app: FastAPI,
    settings: CommonSettings,
    *,
    registry: CollectorRegistry | None = None,
) -> Instrumentator | None:
    if not settings.metrics_enabled:
        logger.info("Metrics disabled —> not exposing %s", settings.metrics_endpoint)
        return None

    instrumentator = Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=False,
        should_group_untemplated=True,
        excluded_handlers=[settings.metrics_endpoint],
        registry=registry,
    )
    instrumentator.instrument(app)
    instrumentator.expose(
        app,
        endpoint=settings.metrics_endpoint,
        include_in_schema=True,
        tags=["observability"],
    )
    logger.info("Metrics exposed at %s", settings.metrics_endpoint)
    return instrumentator
