"""Debounced, cancel-safe RAG prefetch on partial STT transcripts (Phase 5B).

Deepgram emits an interim transcript every few hundred milliseconds, so naively
firing a prefetch per partial would mean a dozen backend calls per utterance.
This scheduler enforces three rules:

* only partials of meaningful length are considered (interim fragments like
  "what" are useless as a retrieval query);
* a partial must have grown enough since the last one we acted on, so a
  re-transcription of the same words does not trigger a second call;
* at most one prefetch is scheduled at a time, and a newer partial cancels the
  pending one before it fires.

Nothing here is awaited by the voice turn: :meth:`PrefetchScheduler.on_partial`
is synchronous, returns immediately, and every prefetch runs on its own task
with all errors swallowed. A prefetch that fails or arrives late costs nothing
because the real turn does its own retrieval anyway.

Free of livekit imports so it is unit-testable without the voice stack.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_CHARS = 12
DEFAULT_DEBOUNCE_SECONDS = 0.35
DEFAULT_MIN_GROWTH_CHARS = 6


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


class PrefetchScheduler:
    """Schedules at most one in-flight prefetch for the newest partial."""

    def __init__(
        self,
        send: Callable[[str], Awaitable[None]],
        *,
        min_chars: int = DEFAULT_MIN_CHARS,
        debounce_seconds: float = DEFAULT_DEBOUNCE_SECONDS,
        min_growth_chars: int = DEFAULT_MIN_GROWTH_CHARS,
        loop_factory: Optional[Callable[[], asyncio.AbstractEventLoop]] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self._send = send
        self._min_chars = min_chars
        self._debounce_seconds = debounce_seconds
        self._min_growth_chars = min_growth_chars
        self._loop_factory = loop_factory
        self._log = log or logger

        self._task: Optional[asyncio.Task] = None
        self._last_dispatched: str = ""
        # Counters make the behaviour observable from tests and logs.
        self.scheduled_count = 0
        self.dispatched_count = 0
        self.skipped_count = 0

    # -- decision logic (pure, easy to assert on) ------------------------- #
    def should_prefetch(self, text: str) -> bool:
        """True when this partial is worth spending a backend call on."""
        candidate = _normalize(text)
        if len(candidate) < self._min_chars:
            return False
        previous = self._last_dispatched
        if not previous:
            return True
        if candidate == previous:
            return False
        # A partial that merely extends the last one by a word or two would
        # retrieve the same chunks; wait for a real change.
        if candidate.startswith(previous) and (
            len(candidate) - len(previous) < self._min_growth_chars
        ):
            return False
        return True

    # -- scheduling ------------------------------------------------------- #
    def on_partial(self, text: str) -> bool:
        """Schedule a prefetch for ``text``. Never blocks, never raises.

        Returns True when a task was scheduled, so callers and tests can tell
        a debounce-skip from a dispatch.
        """
        try:
            if not self.should_prefetch(text):
                self.skipped_count += 1
                return False

            candidate = _normalize(text)
            # Cancel-safe: a newer partial supersedes whatever is pending.
            self.cancel_pending()

            loop = self._resolve_loop()
            if loop is None:
                self.skipped_count += 1
                return False

            self._last_dispatched = candidate
            self.scheduled_count += 1
            self._task = loop.create_task(self._run(text))
            return True
        except Exception:  # noqa: BLE001 - a warm-up must never break a turn
            self._log.exception("Failed to schedule RAG prefetch")
            return False

    def cancel_pending(self) -> None:
        """Drop any prefetch that has not completed yet."""
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self) -> None:
        """Cancel and await the in-flight prefetch, if any."""
        task = self._task
        self._task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            self._log.debug("Prefetch task ended with an error", exc_info=True)

    # -- internals -------------------------------------------------------- #
    def _resolve_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        if self._loop_factory is not None:
            return self._loop_factory()
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            self._log.debug("No running loop; skipping RAG prefetch")
            return None

    async def _run(self, text: str) -> None:
        try:
            if self._debounce_seconds > 0:
                await asyncio.sleep(self._debounce_seconds)
            await self._send(text)
            self.dispatched_count += 1
        except asyncio.CancelledError:
            # Superseded by a newer partial, or the session is closing.
            raise
        except Exception as exc:  # noqa: BLE001 - best effort by design
            self._log.warning("RAG prefetch failed (ignored): %s", exc)


__all__ = [
    "PrefetchScheduler",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_MIN_GROWTH_CHARS",
]
