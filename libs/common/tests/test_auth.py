from datetime import datetime, timedelta, timezone

import jwt
import pytest
from common.auth import (
    decode_access_token,
    optional_auth,
    require_auth,
    require_roles,
    verify_access_token,
)
from common.context import roles_var, user_id_var
from common.errors import AuthError, ForbiddenError
from common.jwt_keys import key_id
from common.settings import CommonSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.requests import Request

pytestmark = pytest.mark.unit

_rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_PRIVATE_PEM = _rsa_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode("utf-8")

_PUBLIC_PEM = (
    _rsa_key.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)


def _settings(**overrides) -> CommonSettings:
    base = dict(jwt_private_key=_PRIVATE_PEM, jwt_public_key=_PUBLIC_PEM, jwt_algorithm="RS256")
    base.update(overrides)
    return CommonSettings(**base)


def _token(claims: dict, *, key: str = _PRIVATE_PEM, algorithm: str = "RS256") -> str:
    return jwt.encode({"typ": "access", **claims}, key, algorithm=algorithm)


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


def _patch_user_watermark(monkeypatch, *, revoked_at: float | None, fail: bool = False) -> None:
    from common import auth as auth_module

    async def fake(user_id: str, *, settings=None) -> float | None:
        if fail:
            raise ConnectionError("redis down")
        return revoked_at

    monkeypatch.setattr(auth_module, "user_tokens_revoked_at", fake)


def test_decode_valid_token_returns_claims() -> None:
    claims = decode_access_token(_token({"sub": "u1"}), _settings())
    assert claims["sub"] == "u1"


def test_decode_expired_token_raises() -> None:
    expired = _token({"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(hours=1)})
    with pytest.raises(AuthError):
        decode_access_token(expired, _settings())


def test_decode_wrong_signature_raises() -> None:
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_private_pem = other_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    forged = _token({"sub": "u1"}, key=other_private_pem, algorithm="RS256")

    with pytest.raises(AuthError):
        decode_access_token(forged, _settings())


def _other_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private, public


def test_decode_uses_kid_to_pick_the_retiring_key_during_rotation() -> None:
    old_private, old_public = _other_keypair()
    signed_by_old = jwt.encode(
        {"sub": "u1", "typ": "access"},
        old_private,
        algorithm="RS256",
        headers={"kid": key_id(old_public)},
    )

    # rotated: old key demoted to previous, current is the new pair
    assert decode_access_token(signed_by_old, _settings(jwt_previous_public_key=old_public))
    # and once the previous key is dropped, the token is dead
    with pytest.raises(AuthError):
        decode_access_token(signed_by_old, _settings())


def test_decode_rejects_unknown_kid() -> None:
    token = jwt.encode(
        {"sub": "u1", "typ": "access"}, _PRIVATE_PEM, algorithm="RS256", headers={"kid": "nope"}
    )
    with pytest.raises(AuthError):
        decode_access_token(token, _settings())


def test_decode_accepts_legacy_token_without_kid_using_current_key() -> None:
    legacy = jwt.encode({"sub": "u1", "typ": "access"}, _PRIVATE_PEM, algorithm="RS256")
    assert "kid" not in jwt.get_unverified_header(legacy)
    assert decode_access_token(legacy, _settings())["sub"] == "u1"


def test_decode_hs256_still_uses_the_shared_secret() -> None:
    secret = "s" * 32
    token = jwt.encode({"sub": "u1", "typ": "access"}, secret, algorithm="HS256")
    settings = CommonSettings(jwt_algorithm="HS256", jwt_secret_key=secret)
    assert decode_access_token(token, settings)["sub"] == "u1"


def test_decode_rejects_refresh_token_as_bearer() -> None:
    refresh = _token({"sub": "u1", "typ": "refresh"})
    with pytest.raises(AuthError):
        decode_access_token(refresh, _settings())


def test_decode_rejects_token_without_typ() -> None:
    untyped = jwt.encode({"sub": "u1"}, _PRIVATE_PEM, algorithm="RS256")
    with pytest.raises(AuthError):
        decode_access_token(untyped, _settings())


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
    just_expired = _token({"sub": "u1", "exp": datetime.now(timezone.utc) - timedelta(seconds=10)})

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


async def test_token_issued_before_user_watermark_is_rejected(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=1_000_000.0)
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'iat': 999_990})}"}

    with pytest.raises(AuthError, match="revoked"):
        await dep(_request(headers))


async def test_token_issued_after_user_watermark_is_accepted(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=1_000_000.0)
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'iat': 1_000_000})}"}

    assert await dep(_request(headers)) == "u1"


async def test_token_without_iat_is_rejected_once_user_has_a_watermark(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=1_000_000.0)
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1'})}"}

    with pytest.raises(AuthError, match="revoked"):
        await dep(_request(headers))


async def test_watermark_lookup_failure_degrades_to_accepting(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=None, fail=True)
    dep = require_auth(settings=_settings())
    headers = {"Authorization": f"Bearer {_token({'sub': 'u1', 'iat': 1})}"}

    assert await dep(_request(headers)) == "u1"


async def test_verify_access_token_returns_claims_for_live_token(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=None)

    claims = await verify_access_token(_token({"sub": "u1", "jti": "j1", "iat": 5}), _settings())

    assert claims["sub"] == "u1" and claims["jti"] == "j1"


async def test_verify_access_token_rejects_denylisted_jti(monkeypatch) -> None:
    _patch_denylist(monkeypatch, contains=True)
    _patch_user_watermark(monkeypatch, revoked_at=None)

    with pytest.raises(AuthError, match="revoked"):
        await verify_access_token(_token({"sub": "u1", "jti": "gone"}), _settings())


async def test_verify_access_token_rejects_token_older_than_user_watermark(monkeypatch) -> None:
    _patch_denylist(monkeypatch)
    _patch_user_watermark(monkeypatch, revoked_at=100.0)

    with pytest.raises(AuthError, match="revoked"):
        await verify_access_token(_token({"sub": "u1", "iat": 99}), _settings())
