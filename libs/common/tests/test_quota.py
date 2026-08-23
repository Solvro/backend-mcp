import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TypeVar

import pytest
from common.context import user_id_var
from common.errors import RateLimitedError
from common.rate_limit import daily_quota
from common.redis import (
    _seconds_until_utc_midnight,
    check_daily_quota,
    close_redis,
    get_redis,
    quota_key,
)
from common.settings import CommonSettings
from starlette.requests import Request
from starlette.responses import Response

pytestmark = pytest.mark.unit

REDIS_URL = "redis://localhost:6379/15"

T = TypeVar("T")
_SKIP = object()


def _run(body: Callable[[CommonSettings], Awaitable[T]]) -> T:
    async def _outer():
        settings = CommonSettings(
            redis_url=REDIS_URL, redis_key_prefix=f"test-{uuid.uuid4().hex[:8]}"
        )
        await close_redis()
        try:
            await get_redis(settings).ping()
        except Exception:
            return _SKIP
        try:
            return await body(settings)
        finally:
            await close_redis()

    result = asyncio.run(_outer())
    if result is _SKIP:
        pytest.skip("Redis not reachable at localhost:6379")
    return result


def test_seconds_until_midnight_one_hour_before() -> None:
    now = datetime(2026, 8, 23, 23, 0, 0, tzinfo=timezone.utc)
    assert _seconds_until_utc_midnight(now) == 3600


def test_seconds_until_midnight_is_at_least_one() -> None:
    now = datetime(2026, 8, 23, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert _seconds_until_utc_midnight(now) >= 1


def test_quota_key_includes_scope_identity_and_day() -> None:
    settings = CommonSettings(redis_key_prefix="mcp")
    key = quota_key("chat:message", "ip:1.2.3.4", "2026-08-23", settings=settings)
    assert key == "mcp:quota:chat:message:ip:1.2.3.4:2026-08-23"


def test_allows_up_to_limit_then_blocks() -> None:
    async def body(settings: CommonSettings):
        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        results = [
            await check_daily_quota(
                "chat:message", "ip:a", limit=3, now=now, settings=settings
            )
            for _ in range(4)
        ]
        return results

    results = _run(body)
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert results[-1].retry_after > 0


def test_quota_is_isolated_per_identity() -> None:
    async def body(settings: CommonSettings):
        now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        await check_daily_quota("chat:message", "ip:a", limit=1, now=now, settings=settings)
        a = await check_daily_quota(
            "chat:message", "ip:a", limit=1, now=now, settings=settings
        )
        b = await check_daily_quota(
            "chat:message", "ip:b", limit=1, now=now, settings=settings
        )
        return a, b

    blocked_a, fresh_b = _run(body)
    assert blocked_a.allowed is False
    assert fresh_b.allowed is True


def test_quota_resets_on_next_day() -> None:
    async def body(settings: CommonSettings):
        day1 = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        await check_daily_quota("chat:message", "ip:a", limit=1, now=day1, settings=settings)
        blocked = await check_daily_quota(
            "chat:message", "ip:a", limit=1, now=day1, settings=settings
        )
        next_day = await check_daily_quota(
            "chat:message", "ip:a", limit=1, now=day2, settings=settings
        )
        return blocked, next_day

    blocked, next_day = _run(body)
    assert blocked.allowed is False
    assert next_day.allowed is True


def _request(host: str = "1.2.3.4") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "headers": [],
        "client": (host, 50000),
    }
    return Request(scope)


def test_dependency_uses_anonymous_limit_when_no_user() -> None:
    async def body(settings: CommonSettings):
        dep = daily_quota(
            "chat:message", settings=settings, anonymous_limit=2, authenticated_limit=9
        )
        token = user_id_var.set(None)
        outcomes = []
        try:
            for _ in range(3):
                response = Response()
                try:
                    await dep(_request(), response)
                    outcomes.append(("ok", response.headers.get("RateLimit-Limit")))
                except RateLimitedError as exc:
                    outcomes.append(("blocked", exc.headers.get("Retry-After")))
        finally:
            user_id_var.reset(token)
        return outcomes

    outcomes = _run(body)
    assert [o[0] for o in outcomes] == ["ok", "ok", "blocked"]
    assert outcomes[0][1] == "2"
    assert outcomes[-1][1] is not None


def test_dependency_uses_authenticated_limit_when_user_present() -> None:
    async def body(settings: CommonSettings):
        dep = daily_quota(
            "chat:message", settings=settings, anonymous_limit=2, authenticated_limit=5
        )
        token = user_id_var.set("user-123")
        allowed = 0
        try:
            for _ in range(5):
                response = Response()
                await dep(_request(), response)
                allowed += 1
        finally:
            user_id_var.reset(token)
        return allowed

    assert _run(body) == 5
