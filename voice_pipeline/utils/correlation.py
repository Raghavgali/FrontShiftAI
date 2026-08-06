"""Correlation headers for voice worker to backend calls (Phase 6A).

The backend generates an ``X-Request-ID`` per request and echoes it back
(``backend/observability/tracing.py``). That is enough to correlate a single
HTTP hop, but a voice turn fans out into several backend tool calls, and the
interesting question during an incident is "what happened in *this
conversation*", not "what happened in this one POST".

So the voice worker sends its LiveKit session id as the correlation value.
The backend honours a client-supplied id verbatim, which makes the session id
the join key across the voice logs and every backend log line the session
produced.

Wiring (one line in ``BackendClient``, owned by voice_pipeline/scripts/main.py)::

    from utils.correlation import correlation_headers

    self.headers = {
        "Authorization": f"Bearer {token}",
        **correlation_headers(session_id),
    }

or, per call, merged into ``extra_headers``::

    await self.post_with_retry(
        path, payload,
        extra_headers=correlation_headers(self.session_id, {"Idempotency-Key": key}),
    )

Keep the sanitiser in sync with ``_coerce_id`` in the backend: an id the
backend rejects is silently replaced by a random one, which quietly breaks
correlation instead of failing loudly.
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional

# Must match backend/observability/tracing.py.
CORRELATION_HEADER = "X-Request-ID"
SESSION_HEADER = "X-Session-ID"

_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_.:-"
)
_MAX_LEN = 128


_DIGEST_LEN = 8


def sanitize_correlation_id(value: Optional[str]) -> Optional[str]:
    """Coerce ``value`` into something the backend will accept, or None.

    LiveKit room and session names can contain characters outside the
    backend's conservative charset (spaces, slashes, ``#``). Those are
    replaced with ``-``, which on its own would let two distinct sessions
    ("a b" and "a/b") collapse into one correlation stream and silently
    interleave their logs. So whenever a character is substituted, or the
    value is truncated, a short digest of the original is appended to keep
    distinct sessions distinct.
    """
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None

    cleaned = "".join(ch if ch in _ALLOWED else "-" for ch in value)
    if cleaned == value and len(cleaned) <= _MAX_LEN:
        return cleaned

    digest = hashlib.sha1(value.encode("utf-8", "replace")).hexdigest()[:_DIGEST_LEN]
    keep = _MAX_LEN - _DIGEST_LEN - 1
    return f"{cleaned[:keep]}-{digest}"


def correlation_headers(
    session_id: Optional[str],
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Headers that let the backend adopt ``session_id`` as its request id.

    Returns ``extra`` unchanged (or an empty dict) when there is no usable
    session id, so callers never have to branch. Both header names are sent:
    ``X-Request-ID`` is what the backend prefers, and ``X-Session-ID``
    survives an intermediary that overwrites request ids with its own.
    """
    headers: Dict[str, str] = dict(extra or {})
    cleaned = sanitize_correlation_id(session_id)
    if cleaned:
        headers.setdefault(CORRELATION_HEADER, cleaned)
        headers.setdefault(SESSION_HEADER, cleaned)
    return headers
