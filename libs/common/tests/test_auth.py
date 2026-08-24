from datetime import datetime, timedelta, timezone

import jwt
import pytest
from common.auth import (
    decode_access_token,
    optional_auth,
    require_auth,
    require_roles,
)
from common.context import roles_var, user_id_var
from common.errors import AuthError, ForbiddenError
from common.settings import CommonSettings
from starlette.requests import Request

pytestmark = pytest.mark.unit

_SECRET = "test-secret-key-at-least-32-bytes-long"


def _settings(**overrides) -> CommonSettings:
    base = dict(jwt_secret_key=_SECRET, jwt_algorithm="HS256")
    base.update(overrides)
    return CommonSettings(**base)


def _token(claims: dict, *, secret: str = _SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=algorithm)


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "headers": raw,
        "client": ("testclient", 50000),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clear_user_ctx():
    ut = user_id_var.set(None)
    rt = roles_var.set(())
    yield
    user_id_var.reset(ut)
    roles_var.reset(rt)


def _patch_denylist(monkeypatch, *, contains: bool = False, fail: bool = False) -> None:
    from common import auth as auth_module

    async def fake(jti: str, *, settings=None) -> bool:
        if fail:
            raise ConnectionError("redis down")
        return contains

    monkeypatch.setattr(auth_module, "is_token_denylisted", fake)


def test_decode_valid_token_returns_claims() -> None:
    claims = decode_access_token(_token({"sub": "u1"}), _settings())
    assert claims["sub"] == "u1"


def test_decode_expired_token_raises() -> None:
    expired = _token({"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(hours=1)})
    with pytest.raises(AuthError):
        decode_access_token(expired, _settings())


def test_decode_wrong_secret_raises() -> None:
    forged = _token({"sub": "u1"}, secret="a-different-secret-key-32-bytes-long!!")
    with pytest.raises(AuthError):
        decode_access_token(forged, _settings())


async def test_optional_auth_without_header_is_anonymous() -> None:
    dep = optional_auth(settings=_settings())
    assert await dep(_request()) is None
    assert user_id_var.get() is None


async def test_optional_auth_with_valid_token_resolves_user() -> None:
    dep = optional_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u42'})}"}

    assert await dep(_request(headers)) == "u42"
    assert user_id_var.get() == "u42"


async def test_optional_auth_with_invalid_token_rejects() -> None:
    dep = optional_auth(settings=_settings())
    headers = {"Authorization": "Bearer not-a-jwt"}

    with pytest.raises(AuthError):
        await dep(_request(headers))


async def test_optional_auth_ignores_non_bearer_scheme() -> None:
    dep = optional_auth(settings=_settings())
    headers = {"Authorization": "Basic abc123"}

    assert await dep(_request(headers)) is None


async def test_require_auth_without_header_rejects() -> None:
    dep = require_auth(settings=_settings())
    with pytest.raises(AuthError):
        await dep(_request())


async def test_require_auth_with_valid_token_resolves_user() -> None:
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u7'})}"}

    assert await dep(_request(headers)) == "u7"


async def test_token_without_subject_rejected() -> None:
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'role': 'admin'})}"}

    with pytest.raises(AuthError):
        await dep(_request(headers))


def test_decode_accepts_matching_issuer_and_audience() -> None:
    settings = _settings(jwt_issuer="auth-service", jwt_audience="chat-service")
    token = _token({"sub": "u1", "iss": "auth-service", "aud": "chat-service"})

    claims = decode_access_token(token, settings)

    assert claims["sub"] == "u1"


def test_decode_rejects_wrong_issuer() -> None:
    settings = _settings(jwt_issuer="auth-service")
    token = _token({"sub": "u1", "iss": "someone-else"})

    with pytest.raises(AuthError):
        decode_access_token(token, settings)


def test_decode_rejects_wrong_audience() -> None:
    settings = _settings(jwt_audience="chat-service")
    token = _token({"sub": "u1", "aud": "other-service"})

    with pytest.raises(AuthError):
        decode_access_token(token, settings)


def test_decode_tolerates_clock_skew_within_leeway() -> None:
    settings = _settings(jwt_leeway_seconds=60)
    just_expired = _token(
        {"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(seconds=10)}
    )

    claims = decode_access_token(just_expired, settings)

    assert claims["sub"] == "u1"


async def test_roles_loaded_into_context() -> None:
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'roles': ['admin', 'user']})}"}

    await dep(_request(headers))

    assert roles_var.get() == ("admin", "user")


async def test_denylisted_token_rejected(monkeypatch) -> None:
    _patch_denylist(monkeypatch, contains=True)

    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'jti': 'revoked-jti'})}"}

    with pytest.raises(AuthError):
        await dep(_request(headers))


async def test_non_denylisted_token_passes(monkeypatch) -> None:
    _patch_denylist(monkeypatch, contains=False)

    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'jti': 'live-jti'})}"}

    assert await dep(_request(headers)) == "u1"


async def test_token_without_jti_skips_denylist(monkeypatch) -> None:
    _patch_denylist(monkeypatch, fail=True)

    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1'})}"}

    assert await dep(_request(headers)) == "u1"


async def test_denylist_fails_open_when_redis_unavailable(monkeypatch) -> None:
    _patch_denylist(monkeypatch, fail=True)

    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'jti': 'any-jti'})}"}

    assert await dep(_request(headers)) == "u1"


async def test_require_roles_allows_holder() -> None:
    dep = require_roles("admin", settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'roles': ['admin']})}"}

    assert await dep(_request(headers)) == "u1"


async def test_require_roles_forbids_missing_role() -> None:
    dep = require_roles("admin", settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'roles': ['user']})}"}

    with pytest.raises(ForbiddenError):
        await dep(_request(headers))


async def test_require_roles_requires_authentication() -> None:
    dep = require_roles("admin", settings=_settings())

    with pytest.raises(AuthError):
        await dep(_request())
