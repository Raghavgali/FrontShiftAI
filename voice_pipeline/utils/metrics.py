import logging
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, Optional

from livekit.agents.metrics import (
    STTMetrics,
    EOUMetrics,
    LLMMetrics,
    TTSMetrics,
    VADMetrics,
)

# Import wandb logger (optional dependency)
try:
    from voice_pipeline.utils.wandb_logger import WandbLogger
    WANDB_LOGGER_AVAILABLE = True
except ImportError:
    WANDB_LOGGER_AVAILABLE = False
    WandbLogger = None  # type: ignore

# Prometheus instruments — best-effort import so the voice worker still
# runs in environments where prometheus_client isn't installed (some
# evaluation/test harnesses pull this module without the full deps).
try:
    from voice_pipeline.observability.metrics import (
        voice_vad_eou_delay_seconds,
        voice_stt_duration_seconds,
        voice_llm_ttft_seconds,
        voice_tts_ttfb_seconds,
        voice_e2e_latency_seconds,
        voice_session_errors_total,
    )
    _PROM_OK = True
except Exception:  # noqa: BLE001
    _PROM_OK = False


logger = logging.getLogger("voice_pipeline.metrics")


# ---------------------------------------------------------------------------
# End-to-end latency tracker
# ---------------------------------------------------------------------------
#
# E2E voice latency = end-of-utterance → first-TTS-byte. The two events are
# emitted by different LiveKit components (STT/VAD vs TTS) but share a
# ``speech_id``. We stash the EOU timestamp keyed by speech_id, then look
# it up when the matching TTS metric arrives.
#
# Bounded so a stuck session can't leak memory.

_E2E_MAX_ENTRIES = 128
_eou_ts_by_speech: "OrderedDict[str, float]" = OrderedDict()
_e2e_lock = threading.Lock()


def _remember_eou(speech_id: Optional[str], ts: float) -> None:
    if not speech_id:
        return
    with _e2e_lock:
        _eou_ts_by_speech[speech_id] = ts
        _eou_ts_by_speech.move_to_end(speech_id)
        while len(_eou_ts_by_speech) > _E2E_MAX_ENTRIES:
            _eou_ts_by_speech.popitem(last=False)


def _pop_eou(speech_id: Optional[str]) -> Optional[float]:
    if not speech_id:
        return None
    with _e2e_lock:
        return _eou_ts_by_speech.pop(speech_id, None)


def _safe_observe(histogram, *label_values, value: float) -> None:
    """Histogram.observe with label binding + swallowed errors."""
    if not _PROM_OK or value is None or value < 0:
        return
    try:
        if label_values:
            histogram.labels(*label_values).observe(value)
        else:
            histogram.observe(value)
    except Exception:  # noqa: BLE001
        pass


def _safe_inc_error(source: str) -> None:
    if not _PROM_OK:
        return
    try:
        voice_session_errors_total.labels(source).inc()
    except Exception:  # noqa: BLE001
        pass


def _serialize_value(value: Any) -> Any:
    """Normalize enums/datetimes so logs stay JSON-serializable."""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _metrics_payload(base_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure timestamp fields are ISO strings for structured logs."""
    payload = {}
    for key, value in base_fields.items():
        payload[key] = _serialize_value(value)
    return payload


class STTMetricsReporter:
    def __init__(self, wandb_logger: Optional[Any] = None) -> None:
        super().__init__()
        self.wandb_logger = wandb_logger

    async def on_stt_metrics_collected(self, metrics: STTMetrics) -> None:
        payload = _metrics_payload(
            {
                "metric_type": str(metrics.type),
                "label": metrics.label,
                "request_id": metrics.request_id,
                "timestamp": datetime.fromtimestamp(metrics.timestamp),
                "duration_seconds": metrics.duration,
                "speech_id": getattr(metrics, "speech_id", None),
                "error": getattr(metrics, "error", None),
                "streamed": metrics.streamed,
                "audio_duration_seconds": metrics.audio_duration,
            }
        )
        logger.info("stt_metrics_collected", extra={"metrics": payload})

        # Prometheus emission (Phase 7B)
        provider = (metrics.label or "unknown").lower()
        _safe_observe(voice_stt_duration_seconds, provider, value=metrics.duration)
        if getattr(metrics, "error", None):
            _safe_inc_error("stt")

        # Log to wandb if available
        if self.wandb_logger:
            self.wandb_logger.log_stt_metrics(
                duration=metrics.duration,
                audio_duration=metrics.audio_duration,
                speech_id=getattr(metrics, "speech_id", None),
                error=getattr(metrics, "error", None),
            )

    async def on_eou_metrics_collected(self, metrics: EOUMetrics) -> None:
        payload = _metrics_payload(
            {
                "metric_type": str(metrics.type),
                "label": metrics.label,
                "timestamp": datetime.fromtimestamp(metrics.timestamp),
                "end_of_utterance_delay_seconds": metrics.end_of_utterance_delay,
                "transcription_delay_seconds": metrics.transcription_delay,
                "speech_id": metrics.speech_id,
                "error": metrics.error,
            }
        )
        logger.info("eou_metrics_collected", extra={"metrics": payload})

        # Prometheus emission + start the E2E timer for this speech_id.
        # We remember the *event* timestamp (= moment of end-of-utterance),
        # then close the loop when the matching TTS first-byte fires.
        _safe_observe(voice_vad_eou_delay_seconds, value=metrics.end_of_utterance_delay)
        _remember_eou(metrics.speech_id, metrics.timestamp)
        if metrics.error:
            _safe_inc_error("stt")


class LLMMetricsReporter:
    def __init__(self, wandb_logger: Optional[Any] = None):
        super().__init__()
        self.wandb_logger = wandb_logger

    async def on_metrics_collected(self, metrics: LLMMetrics) -> None:
        payload = _metrics_payload(
            {
                "metric_type": str(metrics.type),
                "label": metrics.label,
                "request_id": metrics.request_id,
                "timestamp": datetime.fromtimestamp(metrics.timestamp),
                "duration_seconds": metrics.duration,
                "ttft_seconds": metrics.ttft,
                "cancelled": metrics.cancelled,
                "completion_tokens": metrics.completion_tokens,
                "prompt_tokens": metrics.prompt_tokens,
                "total_tokens": metrics.total_tokens,
                "tokens_per_second": metrics.tokens_per_second,
            }
        )
        logger.info("llm_metrics_collected", extra={"metrics": payload})

        # Prometheus emission (Phase 7B): voice agent LLM time-to-first-token.
        _safe_observe(voice_llm_ttft_seconds, value=metrics.ttft)
        if metrics.cancelled:
            _safe_inc_error("llm")

        # Log to wandb if available
        if self.wandb_logger:
            self.wandb_logger.log_llm_metrics(
                duration=metrics.duration,
                ttft=metrics.ttft,
                completion_tokens=metrics.completion_tokens,
                prompt_tokens=metrics.prompt_tokens,
                tokens_per_second=metrics.tokens_per_second,
                error=None,
            )
        


class TTSMetricsReporter:
    def __init__(self, wandb_logger: Optional[Any] = None):
        super().__init__()
        self.wandb_logger = wandb_logger

    async def on_metrics_collected(self, metrics: TTSMetrics) -> None:
        payload = _metrics_payload(
            {
                "metric_type": str(metrics.type),
                "label": metrics.label,
                "request_id": metrics.request_id,
                "timestamp": datetime.fromtimestamp(metrics.timestamp),
                "ttfb_seconds": metrics.ttfb,
                "duration_seconds": metrics.duration,
                "audio_duration_seconds": metrics.audio_duration,
                "cancelled": metrics.cancelled,
                "characters_count": metrics.characters_count,
                "streamed": metrics.streamed,
                "speech_id": metrics.speech_id,
                "error": metrics.error,
            }
        )
        logger.info("tts_metrics_collected", extra={"metrics": payload})

        # Prometheus emission (Phase 7B)
        provider = (metrics.label or "unknown").lower()
        _safe_observe(voice_tts_ttfb_seconds, provider, value=metrics.ttfb)
        if metrics.error:
            _safe_inc_error("tts")

        # End-to-end voice turn (the SLO number): EOU → first TTS byte.
        # ``metrics.timestamp`` is the moment TTS started emitting audio,
        # so the delta from the stored EOU timestamp is the user-perceived
        # silence between "I finished talking" and "agent started talking".
        eou_ts = _pop_eou(metrics.speech_id)
        if eou_ts is not None:
            e2e = max(0.0, metrics.timestamp - eou_ts)
            _safe_observe(voice_e2e_latency_seconds, value=e2e)

        # Log to wandb if available
        if self.wandb_logger:
            self.wandb_logger.log_tts_metrics(
                duration=metrics.duration,
                ttfb=metrics.ttfb,
                audio_duration=metrics.audio_duration,
                characters_count=metrics.characters_count,
                error=metrics.error,
            )
        


class VADMetricsReporter:
    def __init__(self, wandb_logger: Optional[Any] = None):
        super().__init__()
        self.wandb_logger = wandb_logger

    async def on_vad_event(self, event: VADMetrics) -> None:
        payload = _metrics_payload(
            {
                "metric_type": str(event.type),
                "timestamp": datetime.fromtimestamp(event.timestamp),
                "idle_time_seconds": event.idle_time,
                "inference_duration_total_seconds": event.inference_duration_total,
                "inference_count": event.inference_count,
                "speech_id": getattr(event, "speech_id", None),
                "error": getattr(event, "error", None),
            }
        )
        logger.info("vad_event", extra={"metrics": payload})

        # Log to wandb if available
        if self.wandb_logger:
            self.wandb_logger.log_vad_metrics(
                idle_time=event.idle_time,
                inference_duration=event.inference_duration_total,
                inference_count=event.inference_count,
            )


class RAGMetricsReporter:
    """Reporter for RAG query metrics and latency tracking."""

    def __init__(self, wandb_logger: Optional[Any] = None):
        super().__init__()
        self.wandb_logger = wandb_logger

    async def on_rag_query_start(
        self,
        session_id: str,
        query: str,
        top_k: int
    ) -> None:
        """Log when a RAG query starts."""
        payload = {
            "metric_type": "rag_query_start",
            "timestamp": datetime.now(),
            "session_id": session_id,
            "query": query,
            "top_k": top_k,
        }
        logger.info("rag_query_start", extra={"metrics": _metrics_payload(payload)})

    async def on_rag_query_complete(
        self,
        session_id: str,
        query: str,
        total_duration: float,
        backend_duration: float,
        retrieval_duration: float,
        generation_duration: float,
        sources_count: int,
        cache_hit: bool = False,
        error: Any = None
    ) -> None:
        """Log when a RAG query completes with detailed timing breakdown."""
        payload = {
            "metric_type": "rag_query_complete" if not error else "rag_query_error",
            "timestamp": datetime.now(),
            "session_id": session_id,
            "query": query,
            "total_duration_seconds": total_duration,
            "backend_duration_seconds": backend_duration,
            "retrieval_duration_seconds": retrieval_duration,
            "generation_duration_seconds": generation_duration,
            "network_overhead_seconds": total_duration - backend_duration,
            "sources_count": sources_count,
            "cache_hit": cache_hit,
            "error": str(error) if error else None,
        }

        log_method = logger.error if error else logger.info
        log_method(
            "rag_query_complete" if not error else "rag_query_error",
            extra={"metrics": _metrics_payload(payload)}
        )

        # Tool-call counter (Phase 7B): outcome of the RAG tool from the
        # voice agent's perspective. Backend-side RAG metrics live in the
        # backend Prometheus exporter; this is the *agent-side* view.
        if _PROM_OK:
            try:
                from voice_pipeline.observability.metrics import voice_tool_calls_total
                voice_tool_calls_total.labels(
                    "query_info",
                    "error" if error else "success",
                ).inc()
            except Exception:  # noqa: BLE001
                pass

        # Log to wandb if available
        if self.wandb_logger:
            self.wandb_logger.log_rag_metrics(
                query=query,
                total_duration=total_duration,
                backend_duration=backend_duration,
                retrieval_duration=retrieval_duration,
                generation_duration=generation_duration,
                sources_count=sources_count,
                cache_hit=cache_hit,
                error=str(error) if error else None,
            )
