from enum import IntEnum, StrEnum

from redis.asyncio import ConnectionPool, Redis

from common.settings import CommonSettings

_client: Redis | None = None
_key_prefix: str | None = None


def get_redis(settings: CommonSettings | None = None) -> Redis:
    global _client
    if _client is None:
        s = settings or CommonSettings()
        pool = ConnectionPool.from_url(s.redis_url, decode_responses=True)
        _client = Redis.from_pool(pool)
    return _client


async def check_redis() -> bool:
    await get_redis().ping()
    return True


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


class Namespace(StrEnum):
    RATE_LIMIT = "ratelimit"
    DENYLIST = "denylist"
    CACHE = "cache"


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
