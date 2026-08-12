import uuid # noqa
from typing import Callable
from contextvars import ContextVar

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

        headers = dict(scope["headers"])

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

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id_bytes))
                headers.append((b"x-trace-id", trace_id_bytes))
                if session_id_bytes is not None:
                    headers.append((b"x-session-id", session_id_bytes))
                if user_id_bytes is not None:
                    headers.append((b"x-user-id", user_id_bytes))

            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(t1)
            trace_id_var.reset(t2)
            session_id_var.reset(t3)
            user_id_var.reset(t4)
