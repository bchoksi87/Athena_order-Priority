"""structlog configuration and request-context helpers.

JSON rendering in production, a coloured console renderer elsewhere. Request
scoped fields (``request_id``, ``user_id`` ...) are bound through
``structlog.contextvars`` so every log line emitted while handling a request
carries them without threading a logger through the call stack.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

import structlog

from app.core.ids import new_id

REQUEST_ID_HEADER = "x-request-id"
_ASGI_HEADER = b"x-request-id"


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog and the stdlib root logger. Safe to call repeatedly."""
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to ``name`` (typically ``__name__``)."""
    return structlog.get_logger(name)


def bind_request_context(request_id: str, **extra: Any) -> None:
    """Bind request-scoped fields into the structlog context for the current task."""
    structlog.contextvars.bind_contextvars(request_id=request_id, **extra)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


@contextmanager
def request_context(request_id: str, **extra: Any) -> Iterator[None]:
    """Context manager form of :func:`bind_request_context` (cleared on exit)."""
    bind_request_context(request_id, **extra)
    try:
        yield
    finally:
        clear_request_context()


def request_id_from_headers(headers: list[tuple[bytes, bytes]]) -> str:
    """Return the incoming ``X-Request-ID`` or generate a new one."""
    for key, value in headers:
        if key.lower() == _ASGI_HEADER and value:
            return value.decode("latin-1")[:128]
    return new_id("req")


class RequestIdMiddleware:
    """Pure ASGI middleware: binds a request id to logs and echoes it in the response.

    Also records simple request counters on ``scope["app"].state.metrics`` when an
    object with an ``observe(method, path, status)`` method is present there.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = request_id_from_headers(scope.get("headers", []))
        scope.setdefault("state", {})["request_id"] = request_id
        status_holder = {"status": 500}

        async def send_with_header(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 500))
                headers = list(message.get("headers", []))
                headers.append((_ASGI_HEADER, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        bind_request_context(request_id, method=scope.get("method"), path=scope.get("path"))
        try:
            await self._app(scope, receive, send_with_header)
        finally:
            metrics = getattr(getattr(scope.get("app"), "state", None), "metrics", None)
            if metrics is not None:
                metrics.observe(str(scope.get("method")), str(scope.get("path")), status_holder["status"])
            clear_request_context()


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "bind_request_context",
    "clear_request_context",
    "configure_logging",
    "get_logger",
    "request_context",
    "request_id_from_headers",
]
