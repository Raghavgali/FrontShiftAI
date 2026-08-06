"""Bounded reconnect with exponential backoff for the voice session (Phase 5C).

Kept in its own module, free of livekit imports, for two reasons:

1. It is unit-testable without the livekit stack (the voice pipeline has no
   local virtualenv, it only runs on Modal).
2. ``backend/utils/resilience.py`` (the Phase 6.5 policy matrix) is the right
   home for outbound HTTP retry policy, but it is not shipped into the Modal
   voice image and its exponential backoff adds +/-20% jitter. The session
   reconnect contract here is a fixed 1s / 2s / 4s ladder so the worst-case
   time to the "session ended unexpectedly" apology is a known 7s, which is
   what keeps it comfortably inside the Modal supervisor's 60s heartbeat
   watchdog window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


def reconnect_delays(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
) -> List[float]:
    """Return the backoff ladder, one delay per attempt.

    With the defaults this is ``[1.0, 2.0, 4.0]``: wait 1s before the first
    retry, 2s before the second, 4s before the third.
    """

    if max_attempts <= 0:
        return []
    return [base_delay * (2 ** i) for i in range(max_attempts)]


async def reconnect_with_backoff(
    attempt_fn: Callable[[int], Awaitable[None]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    on_give_up: Optional[Callable[[], Awaitable[None]]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Retry ``attempt_fn`` on the backoff ladder until it stops raising.

    Returns True as soon as an attempt succeeds. If every attempt raises,
    ``on_give_up`` is awaited (best effort, its own failure is logged and
    swallowed) and False is returned.

    ``asyncio.CancelledError`` is never swallowed: if the session is being torn
    down while a reconnect is in flight, the teardown wins.
    """

    sleeper = sleep or asyncio.sleep
    log = log or logger
    delays = reconnect_delays(max_attempts=max_attempts, base_delay=base_delay)
    total = len(delays)

    for index, delay in enumerate(delays):
        attempt = index + 1
        await sleeper(delay)
        try:
            await attempt_fn(attempt)
            log.info(
                "Voice session reconnected on attempt %d/%d", attempt, total
            )
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is a retry
            log.warning(
                "Voice session reconnect attempt %d/%d failed: %s",
                attempt,
                total,
                exc,
            )

    log.error(
        "Voice session reconnect gave up after %d attempts", total
    )
    if on_give_up is not None:
        try:
            await on_give_up()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the apology is best effort
            log.exception("Failed to deliver session-ended notice")
    return False


__all__ = [
    "reconnect_delays",
    "reconnect_with_backoff",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_BASE_DELAY_SECONDS",
]
