from typing import Any, Optional

from common.settings import CommonSettings

_settings = CommonSettings()
ERROR_TYPE_BASE_URL = _settings.error_type_base_url.rstrip("/")


class AppError(Exception):
    """Base exception for all application-specific errors."""

    type_uri: str = "about:blank"
    title: str = "Internal Server Error"
    status_code: int = 500
    detail: Any = "An unexpected error occurred."

    def __init__(self, detail: Optional[Any] = None):
        if detail is not None:
            self.detail = detail
        super().__init__(str(self.detail))


class AuthError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/auth-error"
    title = "Authentication Error"
    status_code = 401
    detail = "Authentication failed or credentials were not provided."


class RateLimitedError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/rate-limited"
    title = "Too Many Requests"
    status_code = 429
    detail = "Rate limit exceeded."


class UpstreamError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/upstream-error"
    title = "Upstream Service Error"
    status_code = 502
    detail = "Error communicating with upstream service."


class ValidationError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/validation-error"
    title = "Validation Error"
    status_code = 422
    detail = "The request parameters or body are invalid."


class NotFoundError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/not-found"
    title = "Resource Not Found"
    status_code = 404
    detail = "The requested resource could not be found."
