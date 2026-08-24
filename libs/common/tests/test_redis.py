import pytest
from common import redis as redis_module
from common.redis import (
    TTL,
    Namespace,
    cache_key,
    denylist_key,
    is_token_denylisted,
    make_key,
    rate_limit_key,
    revoke_token,
)
from common.settings import CommonSettings

SETTINGS = CommonSettings(redis_key_prefix="mcp")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, tuple[str, int | None]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, ex)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


@pytest.mark.unit
def test_keys_are_prefixed_and_namespaced():
    assert rate_limit_key("login:user:42", settings=SETTINGS) == "mcp:ratelimit:login:user:42"
    assert denylist_key("jti-abc", settings=SETTINGS) == "mcp:denylist:jti-abc"
    assert cache_key("deadbeef", settings=SETTINGS) == "mcp:cache:deadbeef"


@pytest.mark.unit
def test_features_do_not_collide():
    same_id = "42"
    keys = {
        rate_limit_key(same_id, settings=SETTINGS),
        denylist_key(same_id, settings=SETTINGS),
        cache_key(same_id, settings=SETTINGS),
    }
    assert len(keys) == 3
    for k in keys:
        assert k.startswith("mcp:")


@pytest.mark.unit
def test_prefix_is_configurable():
    other = CommonSettings(redis_key_prefix="staging")
    assert make_key(Namespace.CACHE, "x", settings=other) == "staging:cache:x"


@pytest.mark.unit
def test_ttls_are_ints():
    assert int(TTL.RATE_LIMIT) == 60
    assert int(TTL.DENYLIST) == 3600
    assert int(TTL.CACHE) == 86_400


@pytest.mark.unit
async def test_revoke_token_then_denylisted(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_module, "get_redis", lambda s=None: fake)

    assert await is_token_denylisted("jti-1", settings=SETTINGS) is False

    await revoke_token("jti-1", ttl_seconds=120, settings=SETTINGS)

    stored_value, stored_ttl = fake.store[denylist_key("jti-1", settings=SETTINGS)]
    assert stored_value == "1"
    assert stored_ttl == 120
    assert await is_token_denylisted("jti-1", settings=SETTINGS) is True


@pytest.mark.unit
async def test_revoke_token_defaults_ttl_to_denylist(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_module, "get_redis", lambda s=None: fake)

    await revoke_token("jti-2", settings=SETTINGS)

    _, stored_ttl = fake.store[denylist_key("jti-2", settings=SETTINGS)]
    assert stored_ttl == int(TTL.DENYLIST)
