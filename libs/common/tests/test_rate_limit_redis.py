import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from common.redis import check_rate_limit, close_redis, get_redis
from common.settings import CommonSettings

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


@pytest.mark.unit
def test_allows_up_to_limit_then_blocks():
    async def body(settings: CommonSettings):
        return [
            await check_rate_limit(
                "chat", "user:1", limit=3, window_seconds=60, settings=settings
            )
            for _ in range(4)
        ]

    r1, r2, r3, r4 = _run(body)

    assert [r.allowed for r in (r1, r2, r3)] == [True, True, True]
    assert [r.remaining for r in (r1, r2, r3)] == [2, 1, 0]
    assert r4.allowed is False
    assert r4.remaining == 0
    assert r4.retry_after > 0


@pytest.mark.unit
def test_distinct_scopes_and_callers_have_separate_budgets():
    async def body(settings: CommonSettings):
        chat = await check_rate_limit(
            "chat", "user:1", limit=1, window_seconds=60, settings=settings
        )
        login = await check_rate_limit(
            "login", "user:1", limit=1, window_seconds=60, settings=settings
        )
        other = await check_rate_limit(
            "chat", "user:2", limit=1, window_seconds=60, settings=settings
        )
        return chat, login, other

    chat, login, other = _run(body)
    assert chat.allowed and login.allowed and other.allowed


@pytest.mark.unit
def test_limit_survives_client_restart():
    async def body(settings: CommonSettings):
        await check_rate_limit("chat", "user:9", limit=1, window_seconds=60, settings=settings)
        await close_redis()
        await get_redis(settings).ping()
        return await check_rate_limit(
            "chat", "user:9", limit=1, window_seconds=60, settings=settings
        )

    result = _run(body)
    assert result.allowed is False
