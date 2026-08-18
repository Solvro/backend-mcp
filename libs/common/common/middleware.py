import logging
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.context import request_id_var, session_id_var, trace_id_var, user_id_var
from common.errors import PayloadTooLargeError
from common.exceptions_handlers import build_rfc7807_response
from common.settings import CommonSettings

logger = logging.getLogger(__name__)


class RequestContext:
    """
    Middleware that initializes request-scoped context
    for each incoming HTTP request.

    Sets request and trace IDs in context variables.
    Session and user IDs, when available, are read or set by service layers.
    """
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()

        headers = dict(scope["headers"])

        method = scope.get("method", "").upper()
        path = scope.get("path", "")

        request_id_bytes = headers.get(
            b"x-request-id") or str(uuid.uuid4()).encode()
        trace_id_bytes = headers.get(
            b"x-trace-id") or str(uuid.uuid4()).encode()

        t1 = request_id_var.set(request_id_bytes.decode("utf-8"))
        t2 = trace_id_var.set(trace_id_bytes.decode("utf-8"))
        t3 = session_id_var.set(None)
        t4 = user_id_var.set(None)

        response_status = 500
        process_time = None

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status, process_time
            if message["type"] == "http.response.start":
                response_status = message.get("status", 500)
                process_time = time.perf_counter() - start_time
                process_ms = process_time * 1000

                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id_bytes))
                headers.append((b"x-trace-id", trace_id_bytes))
                headers.append(
                    (b"x-process-time", f"{process_ms:.2f}".encode())
                )

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if process_time is None:
                process_time = time.perf_counter() - start_time

            process_ms = process_time * 1000

            logger.info(
                "Request completed",
                extra={
                    "method": method,
                    "path": path,
                    "status": response_status,
                    "process_time": round(process_ms, 2),
                }
            )

            request_id_var.reset(t1)
            trace_id_var.reset(t2)
            session_id_var.reset(t3)
            user_id_var.reset(t4)


class SecurityHeadersMiddleware:
    """
    Middleware that attaches a standard set of security headers to every
    HTTP response:

    - Strict-Transport-Security (HSTS)
    - X-Content-Type-Options
    - X-Frame-Options
    - Referrer-Policy
    """
    def __init__(self, app: ASGIApp, settings: CommonSettings) -> None:
        self.app = app
        self._headers = self._build_headers(settings)

    @staticmethod
    def _build_headers(settings: CommonSettings) -> list[tuple[bytes, bytes]]:
        hsts = f"max-age={settings.hsts_max_age}"
        if settings.hsts_include_subdomains:
            hsts += "; includeSubDomains"
        if settings.hsts_preload:
            hsts += "; preload"

        return [
            (b"strict-transport-security", hsts.encode()),
            (b"x-content-type-options", settings.content_type_options.encode()),
            (b"x-frame-options", settings.frame_options.encode()),
            (b"referrer-policy", settings.referrer_policy.encode()),
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                existing = {name.lower() for name, _ in headers}
                for name, value in self._headers:
                    if name not in existing:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BodyTooLargeError(Exception):
    """Raised internally when a streamed request body exceeds the limit."""


class BodySizeLimitMiddleware:
    """
    Middleware that rejects requests whose body exceeds max_body_size
    bytes with a 413 Request Entity Too Large response.
    """
    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self.max_body_size:
                await self._reject(scope, receive, send)
                return

        received = 0
        response_started = False

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_size:
                    raise _BodyTooLargeError
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except _BodyTooLargeError:
            if response_started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        request.state.request_id = request_id_var.get()
        error = PayloadTooLargeError()
        response = build_rfc7807_response(
            request=request,
            status=error.status_code,
            title=error.title,
            type_uri=error.type_uri,
            detail=error.detail,
            headers=error.headers,
        )
        await response(scope, receive, send)


def setup_middleware(app: FastAPI, settings: CommonSettings) -> None:
    """
    Set up middleware for the FastAPI microservices.

    Middleware order of execution:
    Starlette executes middleware in reverse order of addition, so the last
    added middleware wraps the inner ones and executes first on incoming
    requests.

    Resulting order, outermost first:
    1. RequestContext        - request/trace IDs, timing, access log
    2. SecurityHeadersMiddleware - security headers on every response
    3. CORSMiddleware        - origin enforcement
    4. BodySizeLimitMiddleware - rejects oversized bodies with 413

    Placing BodySizeLimitMiddleware innermost means its 413 response still
    travels back out through CORS, the security headers, and RequestContext,
    so it is logged and carries the same headers as any other response.

    Args:
        - app (FastAPI): The FastAPI application instance.
        - settings (CommonSettings): The settings object containing CORS,
        security header, and body size configuration.
    """
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_size=settings.max_request_body_size,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=settings.cors_expose_headers
    )

    if settings.security_headers_enabled:
        app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    app.add_middleware(RequestContext)
