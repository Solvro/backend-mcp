import logging
import time
import uuid
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.context import request_id_var, session_id_var, trace_id_var, user_id_var
from common.settings import CommonSettings

logger = logging.getLogger(__name__)


class RequestContext:
    """
    Middleware that initializes request-scoped context
    for each incoming HTTP request.

    Sets request and trace IDs in context variables.
    Session and user IDs, when available, are read or set by service layers.
    """
    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope, receive, send):
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

        async def send_wrapper(message):
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


def setup_middleware(app: FastAPI, settings: CommonSettings) -> None:
    """
    Set up middleware for the FastAPI microservices.

    Middleware order of execution:
    Starlette executes middleware in reverse order of addition,
    so the last added middleware wraps the inner ones and executes first
    on incoming requests.

    Order added below:
    1. CORSMiddleware (added first, executes outer-last)
    2. RequestContext (added second, wraps CORS and executes outer-first)

    Args:
        - app (FastAPI): The FastAPI application instance.
        - settings (CommonSettings): The settings object
        containing CORS configurations.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=settings.cors_expose_headers
    )

    app.add_middleware(RequestContext)
