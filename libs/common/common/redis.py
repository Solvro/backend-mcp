import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum, StrEnum
from math import ceil
from uuid import uuid4

from redis.asyncio import ConnectionPool, Redis
from redis.commands.core import AsyncScript

from common.redis_scripts import RATE_LIMITING_SCRIPT
from common.settings import CommonSettings

_client: Redis | None = None
_rate_limit_script: AsyncScript | None = None
_key_prefix: str | None = None


def get_redis(settings: CommonSettings | None = None) -> Redis:
    global _client, _rate_limit_script
    if _client is None:
        s = settings or CommonSettings()
        pool = ConnectionPool.from_url(s.redis_url, decode_responses=True)
        _client = Redis.from_pool(pool)
        _rate_limit_script = _client.register_script(RATE_LIMITING_SCRIPT)
    return _client


async def check_redis() -> bool:
    await get_redis().ping()
    return True


async def close_redis() -> None:
    global _client, _rate_limit_script
    if _client is not None:
        await _client.aclose()
    _client = None
    _rate_limit_script = None


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int


async def check_rate_limit(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int,
    settings: CommonSettings | None = None,
) -> RateLimitResult:
    settings = settings or CommonSettings()
    get_redis(settings)
    assert _rate_limit_script is not None

    key = rate_limit_key(f"{scope}:{identifier}", settings=settings)

    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    member = f"{now_ms}:{uuid4().hex}"

    allowed, count, oldest_ms = await _rate_limit_script(
        keys=[key],
        args=[now_ms, window_ms, limit, member],
    )

    count = int(count)
    reset_ms = int(oldest_ms) + window_ms
    reset_seconds = max(0, ceil((reset_ms - now_ms) / 1000))
    remaining = max(0, limit - count)

    return RateLimitResult(
        allowed=bool(allowed),
        limit=limit,
        remaining=remaining,
        reset_seconds=reset_seconds,
        retry_after=reset_seconds if not allowed else 0,
    )


class Namespace(StrEnum):
    RATE_LIMIT = "ratelimit"
    DENYLIST = "denylist"
    CACHE = "cache"
    QUOTA = "quota"


class TTL(IntEnum):
    RATE_LIMIT = 60
    DENYLIST = 3600
    CACHE = 86_400


def get_key_prefix(settings: CommonSettings | None = None) -> str:
    global _key_prefix
    if settings is not None:
        return settings.redis_key_prefix
    if _key_prefix is None:
        _key_prefix = CommonSettings().redis_key_prefix
    return _key_prefix


def make_key(namespace: Namespace, *parts: str, settings: CommonSettings | None = None) -> str:
    return ":".join((get_key_prefix(settings), namespace.value, *parts))


def rate_limit_key(scope: str, *, settings: CommonSettings | None = None) -> str:
    return make_key(Namespace.RATE_LIMIT, scope, settings=settings)


def denylist_key(jti: str, *, settings: CommonSettings | None = None) -> str:
    return make_key(Namespace.DENYLIST, jti, settings=settings)


def cache_key(digest: str, *, settings: CommonSettings | None = None) -> str:
    return make_key(Namespace.CACHE, digest, settings=settings)


def quota_key(
    scope: str, identity: str, day: str, *, settings: CommonSettings | None = None
) -> str:
    return make_key(Namespace.QUOTA, scope, identity, day, settings=settings)


@dataclass(frozen=True)
class QuotaResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int


def _seconds_until_utc_midnight(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, ceil((tomorrow - now).total_seconds()))


async def check_daily_quota(
    scope: str,
    identity: str,
    *,
    limit: int,
    now: datetime | None = None,
    settings: CommonSettings | None = None,
) -> QuotaResult:
    redis = get_redis(settings)
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    key = quota_key(scope, identity, day, settings=settings)
    reset = _seconds_until_utc_midnight(now)

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, reset)
        count, _ = await pipe.execute()

    count = int(count)
    allowed = count <= limit
    return QuotaResult(
        allowed=allowed,
        limit=limit,
        remaining=max(0, limit - count),
        reset_seconds=reset,
        retry_after=reset if not allowed else 0,
    )
