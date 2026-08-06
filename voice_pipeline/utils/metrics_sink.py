"""Local metrics sink used when W&B is unavailable (Phase 5D).

Before this existed, a W&B outage (or the far more common case of W&B simply
being disabled) meant every voice metric was dropped on the floor with no
record and no error. Now every metric is appended as one JSON object per line
to ``metrics.jsonl`` in the session log directory, so latency numbers survive
even when the dashboard does not.

Writing is best effort by contract: a full disk or a read-only filesystem must
never interrupt a live call, so :meth:`FileMetricsSink.write` returns False
instead of raising.

Free of livekit and wandb imports so it is unit-testable anywhere.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LOG_DIR_ENV = "VOICE_PIPELINE_LOG_DIR"
DEFAULT_METRICS_FILENAME = "metrics.jsonl"


def session_log_dir(
    session_id: Optional[str] = None,
    log_dir: Optional[Path] = None,
) -> Path:
    """Resolve the directory for this session's artifacts.

    Precedence: explicit argument, then ``VOICE_PIPELINE_LOG_DIR`` (the same
    env var ``utils.logger`` uses), then ``./logs/voice_pipeline``.
    """

    if log_dir is not None:
        base = Path(log_dir)
    else:
        env_dir = os.getenv(LOG_DIR_ENV)
        base = Path(env_dir) if env_dir else Path.cwd() / "logs" / "voice_pipeline"
    return base / session_id if session_id else base


class FileMetricsSink:
    """Append-only JSON-lines metrics writer. Thread-safe, never raises."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        *,
        log_dir: Optional[Path] = None,
        filename: str = DEFAULT_METRICS_FILENAME,
    ) -> None:
        self.session_id = session_id
        self._dir = session_log_dir(session_id, log_dir)
        self._path = self._dir / filename
        self._lock = threading.Lock()
        self._write_failures = 0
        self._warned_about_failure = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def write_failures(self) -> int:
        return self._write_failures

    def write(
        self,
        metrics: Dict[str, Any],
        *,
        reason: Optional[str] = None,
        step: Optional[int] = None,
    ) -> bool:
        """Append one metrics record. Returns False if it could not be written."""

        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
        }
        if reason:
            record["fallback_reason"] = reason
        if step is not None:
            record["step"] = step
        record["metrics"] = metrics

        try:
            line = json.dumps(record, default=str)
        except Exception:  # noqa: BLE001 - unserializable payload, do not crash
            line = json.dumps(
                {
                    "timestamp": record["timestamp"],
                    "session_id": self.session_id,
                    "fallback_reason": reason,
                    "metrics_repr": repr(metrics),
                }
            )

        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            return True
        except Exception as exc:  # noqa: BLE001 - metrics never break a call
            self._write_failures += 1
            if not self._warned_about_failure:
                self._warned_about_failure = True
                logger.error(
                    "Could not write voice metrics to %s: %s. "
                    "Metrics for this session are being dropped.",
                    self._path,
                    exc,
                )
            return False


__all__ = [
    "FileMetricsSink",
    "session_log_dir",
    "LOG_DIR_ENV",
    "DEFAULT_METRICS_FILENAME",
]
