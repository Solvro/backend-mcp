from datetime import datetime, timedelta, timezone

import jwt
import pytest
from common.auth import (
    decode_access_token,
    optional_auth,
    require_auth,
)
from common.context import user_id_var
from common.errors import AuthError
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
    token = user_id_var.set(None)
    yield
    user_id_var.reset(token)


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
