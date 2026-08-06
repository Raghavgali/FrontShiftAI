"""Request correlation IDs.

Phase 7C: every inbound HTTP request gets an ``X-Request-ID`` (generated
if the client didn't send one) that propagates into log lines and back
out on the response. Correlation IDs are the bridge between dashboards
("p95 just spiked at 14:22") and logs ("what exactly happened to this
slow request?").

Logs pick up the ID automatically: :func:`install_log_filter` installs a
``logging`` record factory that stamps ``request_id`` on every record from
the ContextVar, so there is no need to thread the ID through function
signatures.

Phase 6A note: the original implementation attached
:class:`RequestIdLogFilter` to the root logger only. Logger-level filters
run in ``Logger.handle`` on the logger the record *originated* from, and
propagation to ancestors only walks *handlers*, so a root filter never saw
records from module loggers like ``logging.getLogger(__name__)``. Any
formatter referencing ``%(request_id)s`` would then raise
"Formatting field not found in record". A record factory is topology
independent and covers every logger, including third-party ones.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from threading import Lock
from typing import Awaitable, Callable, Optional

from fastapi import Request, Response

_current_request_id: ContextVar[Optional[str]] = ContextVar(
    "current_request_id", default=None
)

_HEADER = "X-Request-ID"

# Public aliases. The voice worker imports the same names (see
# voice_pipeline/utils/correlation.py) so the header spelling lives in one
# place on both sides of the hop.
CORRELATION_HEADER = _HEADER

# Fallback source: the voice worker labels a conversation with a LiveKit
# session id. If it sends that instead of a request id, use it verbatim so a
# single grep spans the voice logs and every backend tool call the session
# made.
SESSION_HEADER = "X-Session-ID"


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

    Kept for call sites that want per-logger or per-handler filtering. The
    global guarantee comes from :func:`install_record_factory`.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = _current_request_id.get() or "-"
        return True


_RECORD_FACTORY_INSTALLED = False
_FACTORY_LOCK = Lock()


def install_record_factory() -> None:
    """Stamp ``request_id`` on every LogRecord created anywhere in-process.

    Idempotent: wrapping the factory twice would still work but would add a
    pointless call per record, and a hot reload would nest wrappers without
    bound.
    """
    global _RECORD_FACTORY_INSTALLED
    with _FACTORY_LOCK:
        if _RECORD_FACTORY_INSTALLED:
            return
        previous = logging.getLogRecordFactory()

        def factory(*args: object, **kwargs: object) -> logging.LogRecord:
            record = previous(*args, **kwargs)  # type: ignore[arg-type]
            record.request_id = _current_request_id.get() or "-"
            return record

        logging.setLogRecordFactory(factory)
        _RECORD_FACTORY_INSTALLED = True


def install_log_filter(logger: Optional[logging.Logger] = None) -> None:
    """Make ``request_id`` available to every formatter in the process.

    Installs the record factory (the part that actually works globally) and
    also attaches the filter to the target logger and its handlers, which
    keeps behaviour sane for anyone who inspects ``logger.filters`` or
    installs handler-level formatters later.
    """
    install_record_factory()

    target = logger if logger is not None else logging.getLogger()
    if not any(isinstance(f, RequestIdLogFilter) for f in target.filters):
        target.addFilter(RequestIdLogFilter())
    for handler in target.handlers:
        if not any(isinstance(f, RequestIdLogFilter) for f in handler.filters):
            handler.addFilter(RequestIdLogFilter())


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """FastAPI middleware: accept/generate an X-Request-ID per request.

    Trusts short, sanely-shaped client-supplied IDs (up to 128 chars of
    ``[A-Za-z0-9_.:-]``) so upstream gateways that already set one stay
    coherent; otherwise falls back to ``X-Session-ID`` (voice worker) and
    finally generates a fresh UUID4-hex.

    Header lookups go through ``request.headers``, which is already
    case-insensitive, so no manual lower-casing is needed.
    """
    rid = (
        _coerce_id(request.headers.get(_HEADER))
        or _coerce_id(request.headers.get(SESSION_HEADER))
        or new_request_id()
    )

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
