import logging
from collections.abc import Awaitable, Callable
from typing import Any

import jwt
from fastapi import Request

from common.context import roles_var, user_id_var
from common.errors import AuthError, ForbiddenError
from common.redis import denylist_key, get_redis
from common.settings import CommonSettings

logger = logging.getLogger(__name__)

AuthDependency = Callable[[Request], Awaitable[str | None]]


def decode_access_token(token: str, settings: CommonSettings) -> dict:
    kwargs: dict[str, Any] = {"leeway": settings.jwt_leeway_seconds}
    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            **kwargs,
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired access token.") from exc


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


def _normalize_roles(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        return tuple(str(role) for role in raw)
    return ()


async def _is_denylisted(jti: str, settings: CommonSettings) -> bool:
    try:
        redis = get_redis(settings)
        return await redis.exists(denylist_key(jti, settings=settings)) > 0
    except Exception:  # noqa: BLE001 - revocation check degrades gracefully
        logger.warning(
            "Denylist lookup failed; accepting token without revocation check",
            exc_info=True,
        )
        return False


async def _resolve_identity(token: str, settings: CommonSettings) -> str:
    claims = decode_access_token(token, settings)
    user_id = claims.get("sub")
    if not user_id:
        raise AuthError("Access token is missing the subject claim.")

    jti = claims.get("jti")
    if jti and await _is_denylisted(str(jti), settings):
        raise AuthError("Access token has been revoked.")

    user_id_var.set(str(user_id))
    roles_var.set(_normalize_roles(claims.get("roles")))
    return str(user_id)


def require_auth(*, settings: CommonSettings) -> AuthDependency:
    async def dependency(request: Request) -> str:
        token = _extract_bearer(request)
        if token is None:
            raise AuthError("Authentication required.")
        return await _resolve_identity(token, settings)

    return dependency


def optional_auth(*, settings: CommonSettings) -> AuthDependency:
    async def dependency(request: Request) -> str | None:
        token = _extract_bearer(request)
        if token is None:
            return None
        return await _resolve_identity(token, settings)

    return dependency


def require_roles(
    *required: str, settings: CommonSettings
) -> Callable[[Request], Awaitable[str]]:
    authenticate = require_auth(settings=settings)

    async def dependency(request: Request) -> str:
        user_id = await authenticate(request)
        held = set(roles_var.get())
        missing = [role for role in required if role not in held]
        if missing:
            raise ForbiddenError(f"Requires role(s): {', '.join(missing)}.")
        return user_id

    return dependency
