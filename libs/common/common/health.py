import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NotRequired, TypedDict

from fastapi import APIRouter, Response

logger = logging.getLogger(__name__)

ProbeFn = Callable[[], Awaitable[bool]]


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class CheckDict(TypedDict):
    status: Literal["up", "down"]
    required: bool
    error: NotRequired[str]


class HealthReportDict(TypedDict):
    status: str
    checks: dict[str, CheckDict]


class HealthResponse(HealthReportDict):
    service: str


class LivenessResponse(TypedDict):
    status: str
    service: str


@dataclass(frozen=True)
class Dependency:
    name: str
    probe: ProbeFn
    required: bool = True
    timeout: float = 3.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    up: bool
    required: bool
    error: str | None = None


@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    checks: list[CheckResult]

    @property
    def http_status(self) -> int:
        return 503 if self.status is HealthStatus.UNHEALTHY else 200

    def to_dict(self) -> HealthReportDict:
        checks: dict[str, CheckDict] = {}
        for check in self.checks:
            entry: CheckDict = {
                "status": "up" if check.up else "down",
                "required": check.required,
            }
            if check.error:
                entry["error"] = check.error
            checks[check.name] = entry
        return {"status": self.status.value, "checks": checks}


async def _run_probe(dep: Dependency) -> CheckResult:
    try:
        async with asyncio.timeout(dep.timeout):
            await dep.probe()
        return CheckResult(name=dep.name, up=True, required=dep.required)
    except Exception as exc:  # noqa: BLE001 - a probe failure is a "down" result, not a 500
        logger.warning("Health probe failed for %s", dep.name, exc_info=True)
        detail = str(exc) or type(exc).__name__
        return CheckResult(name=dep.name, up=False, required=dep.required, error=detail)


async def run_health_checks(dependencies: list[Dependency]) -> HealthReport:
    results = list(await asyncio.gather(*(_run_probe(dep) for dep in dependencies)))

    if any(not r.up and r.required for r in results):
        status = HealthStatus.UNHEALTHY
    elif any(not r.up for r in results):
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.OK

    return HealthReport(status=status, checks=results)


def build_health_router(
    *,
    service_name: str,
    dependencies_provider: Callable[[], list[Dependency]],
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    async def live() -> LivenessResponse:
        return {"status": HealthStatus.OK.value, "service": service_name}

    @router.get("/health/ready")
    async def ready(response: Response) -> HealthResponse:
        report = await run_health_checks(dependencies_provider())
        response.status_code = report.http_status
        return {"service": service_name, **report.to_dict()}

    @router.get("/health")
    async def health(response: Response) -> HealthResponse:
        report = await run_health_checks(dependencies_provider())
        response.status_code = report.http_status
        return {"service": service_name, **report.to_dict()}

    return router
