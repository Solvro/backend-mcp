import uuid # noqa
import time
import logging
from typing import Callable
from contextvars import ContextVar
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from settings import CommonSettings

settings = CommonSettings()
logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


class RequestContext:
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
        session_id_bytes = headers.get(
            b"x-session-id")
        user_id_bytes = headers.get(
            b"x-user-id")

        t1 = request_id_var.set(request_id_bytes.decode("utf-8"))
        t2 = trace_id_var.set(trace_id_bytes.decode("utf-8"))
        t3 = session_id_var.set(
            session_id_bytes.decode("utf-8") if session_id_bytes else None
            )
        t4 = user_id_var.set(
            user_id_bytes.decode("utf-8") if user_id_bytes else None
        )

        response_status = 500

        async def send_wrapper(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 500)
                process_time = time.perf_counter() - start_time

                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id_bytes))
                headers.append((b"x-trace-id", trace_id_bytes))
                headers.append((b"x-process-time", str(process_time).encode()))
                if session_id_bytes is not None:
                    headers.append((b"x-session-id", session_id_bytes))
                if user_id_bytes is not None:
                    headers.append((b"x-user-id", user_id_bytes))

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            process_time = time.perf_counter() - start_time
            logger.info(
                f"{method} {path} completed with status {response_status} in {process_time:.4f} seconds"
            )

            request_id_var.reset(t1)
            trace_id_var.reset(t2)
            session_id_var.reset(t3)
            user_id_var.reset(t4)


def setup_middleware(app: FastAPI) -> None:
    """
    Set up middleware for the FastAPI application.
    """
    app.add_middleware(RequestContext)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
    )
