import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from common.context import user_id_var
from common.errors import RateLimitedError
from common.redis import RateLimitResult, check_rate_limit
from common.settings import CommonSettings

logger = logging.getLogger(__name__)

RateLimitDependency = Callable[[Request, Response], Awaitable[None]]


def _client_identity(request: Request) -> str:
    user_id = user_id_var.get()
    if user_id:
        return f"user:{user_id}"

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return f"ip:{client_ip}"

    if request.client and request.client.host:
        return f"ip:{request.client.host}"

    return "ip:unknown"


def _rate_limit_headers(result: RateLimitResult, *, include_retry_after: bool) -> dict[str, str]:
    headers = {
        "RateLimit-Limit": str(result.limit),
        "RateLimit-Remaining": str(result.remaining),
        "RateLimit-Reset": str(result.reset_seconds),
    }
    if include_retry_after:
        headers["Retry-After"] = str(result.retry_after)
    return headers


def rate_limit(
    scope: str,
    *,
    settings: CommonSettings,
    limit: int | None = None,
    window_seconds: int | None = None,
) -> RateLimitDependency:
    effective_limit = limit if limit is not None else settings.rate_limit
    effective_window = (
        window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
    )

    async def dependency(request: Request, response: Response) -> None:
        if not settings.rate_limit_enabled:
            return

        identity = _client_identity(request)
        result = await check_rate_limit(
            scope,
            identity,
            limit=effective_limit,
            window_seconds=effective_window,
            settings=settings,
        )

        response.headers.update(_rate_limit_headers(result, include_retry_after=False))

        if not result.allowed:
            logger.warning(
                "Rate limit exceeded",
                extra={"scope": scope, "identity": identity, "limit": effective_limit},
            )
            raise RateLimitedError(
                headers=_rate_limit_headers(result, include_retry_after=True),
            )

    return dependency
