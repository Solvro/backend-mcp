import pytest
from common.redis import (
    TTL,
    Namespace,
    cache_key,
    denylist_key,
    make_key,
    rate_limit_key,
)
from common.settings import CommonSettings

SETTINGS = CommonSettings(redis_key_prefix="mcp")


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
