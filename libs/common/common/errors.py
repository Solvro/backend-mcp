from typing import Any, Optional


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
    type_uri = "https://api.yourdomain.com/errors/auth-error"
    title = "Authentication Error"
    status_code = 401
    detail = "Authentication failed or credentials were not provided."


class RateLimited(AppError):
    type_uri = "https://api.yourdomain.com/errors/rate-limited"
    title = "Too Many Requests"
    status_code = 429
    detail = "Rate limit exceeded."


class UpstreamError(AppError):
    type_uri = "https://api.yourdomain.com/errors/upstream-error"
    title = "Upstream Service Error"
    status_code = 502
    detail = "Error communicating with upstream service."


class ValidationError(AppError):
    type_uri = "https://api.yourdomain.com/errors/validation-error"
    title = "Validation Error"
    status_code = 422
    detail = "The request parameters or body are invalid."


class NotFound(AppError):
    type_uri = "https://api.yourdomain.com/errors/not-found"
    title = "Resource Not Found"
    status_code = 404
    detail = "The requested resource could not be found."
