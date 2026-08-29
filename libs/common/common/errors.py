from typing import Any

from common.settings import CommonSettings

_settings = CommonSettings()
ERROR_TYPE_BASE_URL = _settings.error_type_base_url.rstrip("/")


class AppError(Exception):
    """Base exception for all application-specific errors."""

    type_uri: str = "about:blank"
    title: str = "Internal Server Error"
    status_code: int = 500
    detail: Any = "An unexpected error occurred."
    headers: dict[str, str] | None = None

    def __init__(
        self,
        detail: Any | None = None,
        *,
        headers: dict[str, str] | None = None,
    ):
        if detail is not None:
            self.detail = detail
        if headers is not None:
            self.headers = headers
        super().__init__(str(self.detail))


class AuthError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/auth-error"
    title = "Authentication Error"
    status_code = 401
    detail = "Authentication failed or credentials were not provided."


class ForbiddenError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/forbidden"
    title = "Forbidden"
    status_code = 403
    detail = "You do not have permission to perform this action."


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


class ServiceUnavailableError(UpstreamError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/service-unavailable"
    title = "Service Unavailable"
    status_code = 503
    detail = "The upstream service is temporarily unavailable. Please try again shortly."


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


class PayloadTooLargeError(AppError):
    type_uri = f"{ERROR_TYPE_BASE_URL}/payload-too-large"
    title = "Payload Too Large"
    status_code = 413
    detail = "The request body exceeds the maximum allowed size."
