"""Request correlation IDs.

Phase 7C: every inbound HTTP request gets an ``X-Request-ID`` (generated
if the client didn't send one) that propagates into log lines and back
out on the response. Correlation IDs are the bridge between dashboards
("p95 just spiked at 14:22") and logs ("what exactly happened to this
slow request?").

Logs pick up the ID automatically by instantiating the
:class:`RequestIdLogFilter` on any logger — the filter reads the
ContextVar at emit time, so there's no need to thread the ID through
your function signatures.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable, Optional

from fastapi import Request, Response

_current_request_id: ContextVar[Optional[str]] = ContextVar(
    "current_request_id", default=None
)

_HEADER = "X-Request-ID"


def get_request_id() -> Optional[str]:
    """Return the correlation ID bound to the current request (if any)."""
    return _current_request_id.get()


def set_request_id(value: Optional[str]) -> None:
    _current_request_id.set(value)


def new_request_id() -> str:
    """Uniform 32-char hex id. Client-supplied values are preferred when sane."""
    return uuid.uuid4().hex


class RequestIdLogFilter(logging.Filter):
    """Attach ``request_id`` to every ``LogRecord`` from the ContextVar.

    Install once at app startup so log formatters can reference
    ``%(request_id)s`` without callers having to pass it around.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = _current_request_id.get() or "-"
        return True


def install_log_filter(logger: Optional[logging.Logger] = None) -> None:
    """Attach the filter to the given logger (default: root)."""
    target = logger if logger is not None else logging.getLogger()
    # Avoid stacking duplicate filters on hot reloads.
    for existing in target.filters:
        if isinstance(existing, RequestIdLogFilter):
            return
    target.addFilter(RequestIdLogFilter())


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """FastAPI middleware: accept/generate an X-Request-ID per request.

    Trusts short, sanely-shaped client-supplied IDs (up to 128 chars of
    ``[A-Za-z0-9_.:-]``) so upstream gateways that already set one stay
    coherent; otherwise generates a fresh UUID4-hex.
    """
    incoming = request.headers.get(_HEADER) or request.headers.get(_HEADER.lower())
    rid = _coerce_id(incoming) or new_request_id()

    token = _current_request_id.set(rid)
    try:
        response = await call_next(request)
    finally:
        _current_request_id.reset(token)

    response.headers[_HEADER] = rid
    return response


def _coerce_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    if not 1 <= len(value) <= 128:
        return None
    # Conservative charset: avoids weird bytes landing in logs / metrics labels.
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    if any(ch not in allowed for ch in value):
        return None
    return value
