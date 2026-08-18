import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.errors import AppError, ValidationError

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    """Extract request_id from state/headers or generate a UUID fallback."""
    if hasattr(request.state, "request_id") and request.state.request_id:
        return request.state.request_id

    header_id = request.headers.get("X-Request-ID")
    if header_id:
        return header_id

    return str(uuid.uuid4())


def build_rfc7807_response(
    request: Request,
    status: int,
    title: str,
    type_uri: str,
    detail: Any,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Constructs a strict RFC 7807 compliant JSON response."""
    content = {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": jsonable_encoder(detail),
        "request_id": _get_request_id(request),
    }
    return JSONResponse(
        status_code=status,
        content=content,
        media_type="application/problem+json",
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """
    Registers standard RFC 7807 error handlers on a FastAPI application.
    Shared across auth-service and chat-service.
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(
                "Unhandled application error",
                exc_info=exc,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "request_id": _get_request_id(request),
                },
            )
        return build_rfc7807_response(
            request=request,
            status=exc.status_code,
            title=exc.title,
            type_uri=exc.type_uri,
            detail=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return build_rfc7807_response(
            request=request,
            status=ValidationError.status_code,
            title=ValidationError.title,
            type_uri=ValidationError.type_uri,
            detail=jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return build_rfc7807_response(
            request=request,
            status=exc.status_code,
            title="HTTP Exception",
            type_uri="about:blank",
            detail=exc.detail,
        )

    @app.exception_handler(Exception)
    async def handle_uncaught_exception(request: Request, exc: Exception) -> JSONResponse:
        request_id = _get_request_id(request)
        logger.error(
            f"Unhandled Exception on {request.method} {request.url.path} [request_id={request_id}]",
            exc_info=exc,
        )
        return build_rfc7807_response(
            request=request,
            status=500,
            title="Internal Server Error",
            type_uri="about:blank",
            detail="An unexpected internal server error occurred.",
        )
