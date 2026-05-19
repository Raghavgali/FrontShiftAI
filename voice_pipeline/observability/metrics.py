"""Prometheus metrics for the LiveKit voice worker.

Runs inside each Modal voice_worker_for_room() container (one per session)
and in the local dev worker. Exposes a ``/metrics`` endpoint on a background
HTTP server so the Grafana Agent can scrape per-worker process stats.

**Per-stage histograms** are the core of this module — each user turn
flows VAD → STT → LLM → TTS and we record time-in-stage separately. The
Phase 7F voice dashboard renders them stacked so the bottleneck in a slow
turn is visually obvious.

The :func:`start_metrics_server` helper should be called once early in
worker startup; a second call is a no-op (the server is tracked on a
module-level flag to survive LiveKit's re-entry quirks).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-stage latencies (seconds)
# ---------------------------------------------------------------------------

voice_vad_eou_delay_seconds = Histogram(
    "voice_vad_eou_delay_seconds",
    "Silero VAD end-of-utterance → STT-finalize delay.",
    buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0),
)

voice_stt_duration_seconds = Histogram(
    "voice_stt_duration_seconds",
    "Speech-to-text total processing time per utterance.",
    labelnames=("provider",),
    buckets=(0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0),
)

voice_llm_ttft_seconds = Histogram(
    "voice_llm_ttft_seconds",
    "Voice-agent LLM time-to-first-token.",
    buckets=(0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0),
)

voice_tts_ttfb_seconds = Histogram(
    "voice_tts_ttfb_seconds",
    "Text-to-speech time-to-first-byte.",
    labelnames=("provider",),
    buckets=(0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0),
)

voice_e2e_latency_seconds = Histogram(
    "voice_e2e_latency_seconds",
    "End-of-user-speech → first-TTS-byte — the SLO-relevant number.",
    buckets=(0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)

# ---------------------------------------------------------------------------
# Session-level gauges + counters
# ---------------------------------------------------------------------------

voice_session_active = Gauge(
    "voice_session_active",
    "Currently active voice sessions (per worker process).",
)

voice_tool_calls_total = Counter(
    "voice_tool_calls_total",
    "Voice agent tool invocations by outcome.",
    labelnames=("tool", "outcome"),  # outcome in {"success","error","fallback"}
)

voice_session_errors_total = Counter(
    "voice_session_errors_total",
    "Voice session errors by source.",
    labelnames=("source",),  # {"stt","tts","llm","livekit","backend","auth"}
)

# ---------------------------------------------------------------------------
# /metrics server (started once per worker process)
# ---------------------------------------------------------------------------

_server_lock = threading.Lock()
_server_started = False


def start_metrics_server(port: Optional[int] = None) -> None:
    """Start the Prometheus exposition endpoint.

    Safe to call multiple times — only the first call binds a socket.
    The port is configurable via ``VOICE_METRICS_PORT`` (default 9100)
    so the Grafana Agent scrape target doesn't clash with anything else.
    """
    global _server_started
    if _server_started:
        return
    with _server_lock:
        if _server_started:
            return
        bind_port = int(port if port is not None else os.getenv("VOICE_METRICS_PORT", "9100"))
        try:
            start_http_server(bind_port)
            logger.info("voice metrics server listening on :%d/metrics", bind_port)
            _server_started = True
        except OSError as exc:
            # Port collision (Modal may recycle the container or the dev
            # runner may spawn twice). Log and move on — missing metrics are
            # strictly better than a crashed voice worker.
            logger.warning("voice metrics server could not bind :%d — %s", bind_port, exc)
