import logging
from collections.abc import Awaitable, Callable

import jwt
from fastapi import Request

from common.context import user_id_var
from common.errors import AuthError
from common.settings import CommonSettings

logger = logging.getLogger(__name__)

AuthDependency = Callable[[Request], Awaitable[str | None]]


def decode_access_token(token: str, settings: CommonSettings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
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


def _resolve_user_id(token: str, settings: CommonSettings) -> str:
    claims = decode_access_token(token, settings)
    user_id = claims.get("sub")
    if not user_id:
        raise AuthError("Access token is missing the subject claim.")
    user_id_var.set(str(user_id))
    return str(user_id)


def require_auth(*, settings: CommonSettings) -> AuthDependency:
    async def dependency(request: Request) -> str:
        token = _extract_bearer(request)
        if token is None:
            raise AuthError("Authentication required.")
        return _resolve_user_id(token, settings)

    return dependency


def optional_auth(*, settings: CommonSettings) -> AuthDependency:
    async def dependency(request: Request) -> str | None:
        token = _extract_bearer(request)
        if token is None:
            return None
        return _resolve_user_id(token, settings)

    return dependency
