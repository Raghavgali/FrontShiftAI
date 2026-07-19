import asyncio as _asyncio
import json
import logging
import os
import sys
import uuid
import httpx
import time
from pathlib import Path
from typing import Any, Dict, Optional
from types import SimpleNamespace

from dotenv import load_dotenv

from livekit.agents import Agent, AgentSession, inference, RunContext
from livekit.agents import JobContext, WorkerOptions, cli, vad
from livekit.agents import llm, stt, tts
from livekit.agents.utils.audio import audio_frames_from_file
from livekit.agents.voice.events import CloseEvent, ErrorEvent
from livekit.agents.llm import function_tool
from livekit.agents.metrics import STTMetrics, EOUMetrics, LLMMetrics, TTSMetrics
from livekit.plugins import silero, deepgram, openai, cartesia, assemblyai
# Import BackgroundAudioPlayer for thinking sounds
try:
    from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip
    BACKGROUND_AUDIO_AVAILABLE = True
except ImportError:
    BACKGROUND_AUDIO_AVAILABLE = False
    BackgroundAudioPlayer = None  # type: ignore
    AudioConfig = None  # type: ignore
    BuiltinAudioClip = None  # type: ignore

# Local asyncio shim so tests can monkeypatch without affecting global asyncio
asyncio = SimpleNamespace(
    sleep=_asyncio.sleep,
    create_task=_asyncio.create_task,
    get_event_loop=_asyncio.get_event_loop,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voice_pipeline.utils.config import (
    ProviderChainConfig,
    ProviderConfig,
    load_voice_config,
)
from voice_pipeline.utils.metrics import (
    STTMetricsReporter,
    LLMMetricsReporter,
    TTSMetricsReporter,
    VADMetricsReporter,
    RAGMetricsReporter,
)
from voice_pipeline.utils.wandb_logger import WandbLogger

from voice_pipeline.utils.logger import setup_logging

# Phase 7B: start the Prometheus /metrics server for this worker. Safe to
# import even if prometheus_client isn't installed — the observability
# module itself doesn't fail at import time, only start_metrics_server() does.
try:
    from voice_pipeline.observability.metrics import (
        start_metrics_server,
        voice_session_active,
        voice_tool_calls_total,
        voice_session_errors_total,
    )
    start_metrics_server()
    _PROM_OK = True
except Exception:  # noqa: BLE001
    # Metrics are strictly additive; a missing dependency should never
    # prevent the voice worker from starting.
    _PROM_OK = False
    voice_session_active = None  # type: ignore[assignment]
    voice_tool_calls_total = None  # type: ignore[assignment]
    voice_session_errors_total = None  # type: ignore[assignment]


def _voice_tool_inc(tool: str, outcome: str) -> None:
    """Increment voice_tool_calls_total — silently no-ops if Prometheus isn't loaded."""
    if not _PROM_OK or voice_tool_calls_total is None:
        return
    try:
        voice_tool_calls_total.labels(tool, outcome).inc()
    except Exception:  # noqa: BLE001
        pass


def _voice_error_inc(source: str) -> None:
    if not _PROM_OK or voice_session_errors_total is None:
        return
    try:
        voice_session_errors_total.labels(source).inc()
    except Exception:  # noqa: BLE001
        pass

load_dotenv(dotenv_path=Path(__file__).parent / '.env')
CONFIG = load_voice_config()


def _env_flag(name: str, default: str = "0") -> bool:
    """Return True if the named env var is a truthy flag."""
    raw_value = os.getenv(name, default).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


VOICE_PIPELINE_LOG_DIR = PROJECT_ROOT / "logs" / "voice_pipeline"
setup_logging(
    level=os.getenv("VOICE_PIPELINE_LOG_LEVEL"),
    to_file=_env_flag("VOICE_PIPELINE_LOG_TO_FILE"),
    log_dir=VOICE_PIPELINE_LOG_DIR,
)

logger = logging.getLogger(__name__)


# Endpoints that mutate server state and therefore need idempotency keys on
# retries. Read-only paths (e.g. /api/rag/query) are safe to replay without
# a key. Kept here so BackendClient can auto-generate keys without each tool
# caller having to remember.
_MUTATION_PATH_PREFIXES = (
    "/api/pto/chat",
    "/api/hr-tickets/chat",
    "/api/chat/message",
)


def _is_mutation_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _MUTATION_PATH_PREFIXES)


class BackendClient:
    """HTTP client for backend API calls with JWT authentication."""
    
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
            ),
        )
        logger.info(f"🔗 BackendClient initialized: {self.base_url}")
        logger.info(f"🔑 Token preview: {token[:20]}...{token[-10:]}" if len(token) > 30 else f"🔑 Token: {token}")

    def update_token(self, new_token: str) -> None:
        self.token = new_token
        self.headers = {"Authorization": f"Bearer {new_token}"}
        self._client.headers["Authorization"] = f"Bearer {new_token}"

    async def post(
        self,
        path: str,
        payload: dict,
        timeout: float = 120,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> dict:
        """Make POST request with configurable timeout."""
        logger.info(f"📤 POST {self.base_url}{path}")
        resp = await self._client.post(
            path,
            json=payload,
            headers=extra_headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    
    async def stream_post(
        self,
        path: str,
        payload: dict,
        timeout: float = 10,
        extra_headers: Optional[Dict[str, str]] = None,
    ):
        """POST and yield parsed SSE events."""
        logger.info(f"STREAM POST {self.base_url}{path}")

        async with self._client.stream(
            "POST",
            path,
            json=payload,
            headers=extra_headers,
            timeout=timeout,
        ) as response:
            response.raise_for_status()

            event_name = "message"
            data_lines = []

            async for line in response.aiter_lines():
                if line.startswith(":"):
                    # SSE heartbeat/comment
                    continue
            
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                    continue

                if line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
                    continue

                if line == "" and data_lines:
                    raw_data = "\n".join(data_lines)

                    try:
                        data = json.loads(raw_data)
                    except json.JSONDecodeError:
                        data = raw_data

                    yield {
                        "event": event_name,
                        "data": data,
                    }

                    event_name = "message"
                    data_lines = []

    async def collect_stream(
        self,
        path: str,
        payload: dict,
        total_timeout: float = 10.0,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> dict:
        """Consume an SSE stream within a wall-clock budget (Phase 2C).

        Never raises. Returns {"events": [...], "error": str|None,
        "timed_out": bool} so callers can salvage partial output. Stops at
        the first done/error event.
        """
        events: list = []
        error: Optional[str] = None
        timed_out = False
        agen = self.stream_post(
            path, payload, timeout=total_timeout, extra_headers=extra_headers
        )
        deadline = time.time() + total_timeout
        try:
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    event = await _asyncio.wait_for(agen.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                events.append(event)
                if event.get("event") in ("done", "error"):
                    break
        except _asyncio.TimeoutError:
            timed_out = True
        except Exception as exc:
            error = str(exc)
        finally:
            try:
                await agen.aclose()
            except Exception:
                pass

        if error is None:
            for event in events:
                if event.get("event") == "error":
                    data = event.get("data")
                    error = (
                        data.get("detail", "stream error")
                        if isinstance(data, dict)
                        else str(data)
                    )
        return {"events": events, "error": error, "timed_out": timed_out}

    async def post_with_retry(
        self,
        path: str,
        payload: dict,
        timeout: float = 120,
        max_retries: int = 2,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """POST with retries + a graceful fallback payload on final failure.

        If ``idempotency_key`` is provided (or one is generated when omitted for
        a mutation-style path), it is sent as the ``Idempotency-Key`` header on
        *every* retry of the same logical call — the backend then dedupes so a
        transient network error doesn't create duplicate PTO/HR records.
        """
        # Auto-generate a key for known mutation endpoints when the caller
        # didn't supply one. Read-only endpoints (RAG) don't need it.
        if idempotency_key is None and _is_mutation_path(path):
            idempotency_key = str(uuid.uuid4())

        extra_headers: Optional[Dict[str, str]] = None
        if idempotency_key:
            extra_headers = {"Idempotency-Key": idempotency_key}

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await self.post(
                    path, payload, timeout=timeout, extra_headers=extra_headers
                )
            except Exception as e:
                last_exc = e
                if attempt == max_retries:
                    logger.error(
                        f"Tool call {path} failed after {max_retries + 1} attempts: {e}"
                    )
                    return {
                        "answer": "I'm having trouble looking that up right now. Please try again.",
                        "sources": [],
                        "error": True,
                        "detail": str(e),
                    }
                backoff = 1.0 * (attempt + 1)
                logger.warning(
                    f"Tool call {path} attempt {attempt + 1}/{max_retries + 1} failed: {e}; retrying in {backoff}s"
                )
                await _asyncio.sleep(backoff)
        # Unreachable, but keeps type-checkers quiet.
        raise RuntimeError(f"post_with_retry exited unexpectedly: {last_exc}")

    async def health_check(self) -> bool:
        """Check if backend is reachable."""
        try:
            resp = await self._client.get("/health", timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Backend health check failed: {e}")
            return False

    async def close(self) -> None:
        await self._client.aclose()

def _safe_isinstance(obj: Any, candidate) -> bool:
    """Guard isinstance to tolerate monkeypatched placeholders."""
    try:
        return isinstance(obj, candidate)
    except TypeError:
        return False


def _is_stt_source(source: Optional[Any]) -> bool:
    """Return True if the error source looks like an STT component."""
    if not source:
        return False
    stt_type = getattr(stt, "STT", None)
    if stt_type and _safe_isinstance(source, stt_type):
        return True
    return source.__class__.__name__.lower().endswith("stt")


def _build_stt_provider(cfg: ProviderConfig):
    provider = (cfg.provider or "").lower()
    if provider == "deepgram":
        return deepgram.STT(model=cfg.model, **cfg.kwargs)
    if provider == "assemblyai":
        return assemblyai.STT(model=cfg.model, **cfg.kwargs)
    raise ValueError(f"Unsupported STT provider: {cfg.provider}")


def _build_llm_provider(cfg: ProviderConfig):
    provider = (cfg.provider or "").lower()
    if provider == "openai":
        return openai.LLM(model=cfg.model, **cfg.kwargs)
    if provider == "inference":
        return inference.LLM(model=cfg.model, **cfg.kwargs)
    raise ValueError(f"Unsupported LLM provider: {cfg.provider}")


def _build_tts_provider(cfg: ProviderConfig):
    provider = (cfg.provider or "").lower()
    if provider == "cartesia":
        return cartesia.TTS(model=cfg.model, **cfg.kwargs)
    if provider == "openai":
        return openai.TTS(model=cfg.model, **cfg.kwargs)
    if provider == "deepgram":
        return deepgram.TTS(model=cfg.model, **cfg.kwargs)
    raise ValueError(f"Unsupported TTS provider: {cfg.provider}")


def _build_vad(cfg: ProviderConfig):
    provider = (cfg.provider or "").lower()
    if provider == "silero":
        return silero.VAD.load(**cfg.kwargs)
    raise ValueError(f"Unsupported VAD provider: {cfg.provider}")


def _build_chain(chain_cfg: ProviderChainConfig, builder, adapter_cls):
    providers = [builder(chain_cfg.primary)] + [
        builder(cfg) for cfg in chain_cfg.fallbacks
    ]
    return providers[0] if len(providers) == 1 else adapter_cls(providers)


def _build_stt_chain(chain_cfg: ProviderChainConfig):
    return _build_chain(chain_cfg, _build_stt_provider, stt.FallbackAdapter)


def _build_llm_chain(chain_cfg: ProviderChainConfig):
    return _build_chain(chain_cfg, _build_llm_provider, llm.FallbackAdapter)


def _build_tts_chain(chain_cfg: ProviderChainConfig):
    return _build_chain(chain_cfg, _build_tts_provider, tts.FallbackAdapter)


async def _emit_worker_heartbeat(interval_seconds: float = 30.0) -> None:
    """Emit output so the Modal watchdog knows the event loop is responsive."""
    try:
        while True:
            logger.info("WORKER_HEARTBEAT")
            await _asyncio.sleep(interval_seconds)
    except _asyncio.CancelledError:
        logger.info("Worker heartbeat stopped")
        raise


def extract_user_token_from_room(room) -> Optional[Dict[str, Any]]:
    """
    Extract user token and metadata from room participants.
    
    The frontend embeds the user's backend JWT in the LiveKit participant metadata
    when creating the session. This function extracts it so the voice agent can
    make authenticated API calls on behalf of the user.
    
    Returns:
        Dict with 'user_token', 'user_email', 'company', 'session_id' or None
    """
    logger.info(f"🔍 Checking {len(room.remote_participants)} remote participants for user token")
    
    # Check remote participants (the user who joined)
    for identity, participant in room.remote_participants.items():
        logger.info(f"   Participant: {identity}, metadata: {participant.metadata[:100] if participant.metadata else 'None'}...")
        metadata_str = participant.metadata
        if metadata_str:
            try:
                metadata = json.loads(metadata_str)
                user_token = metadata.get("user_token")
                if user_token:
                    logger.info(
                        f"🔑 FOUND user token from participant: {identity}",
                        extra={
                            "participant": participant.identity,
                            "user_email": metadata.get("user_email"),
                            "company": metadata.get("company"),
                        }
                    )
                    return {
                        "user_token": user_token,
                        "user_email": metadata.get("user_email"),
                        "company": metadata.get("company"),
                        "session_id": metadata.get("session_id"),
                    }
                else:
                    logger.warning(f"   Metadata parsed but no user_token field")
            except json.JSONDecodeError as e:
                logger.warning(f"   Failed to parse participant metadata: {e}")
                continue
    
    logger.warning("❌ No user token found in any participant metadata")
    return None


async def wait_for_user_token(room, max_wait: float = 10.0, check_interval: float = 0.5) -> Optional[Dict[str, Any]]:
    """
    Wait for a user with a valid token to join the room.
    
    The worker might start before the user connects, so we need to wait
    and periodically check for participants with valid metadata.
    """
    logger.info(f"⏳ Waiting up to {max_wait}s for user with token to join...")
    
    elapsed = 0.0
    while elapsed < max_wait:
        user_metadata = extract_user_token_from_room(room)
        if user_metadata and user_metadata.get("user_token"):
            logger.info(f"✅ User token found after {elapsed:.1f}s")
            return user_metadata
        
        await asyncio.sleep(check_interval)
        elapsed += check_interval
        
        if elapsed % 2 == 0:  # Log every 2 seconds
            logger.info(f"   Still waiting... ({elapsed:.1f}s elapsed, {len(room.remote_participants)} participants)")
    
    logger.warning(f"⚠️ Timeout waiting for user token after {max_wait}s")
    return None


class VoiceAgent(Agent):
    """
    VoiceAgent extends LiveKit's Agent, adding backend integrations and metric hooks.
    
    Supports dynamic JWT token injection for multi-tenant authentication:
    - Default: uses service account token from config
    - Runtime: can update token when user joins and their JWT is extracted
    """

    _instructions_override: Optional[str] = None
    _stt_override: Optional[stt.STT] = None
    _llm_override: Optional[llm.LLM] = None
    _tts_override: Optional[tts.TTS] = None
    _vad_override: Optional[vad.VAD] = None

    @property
    def instructions(self) -> Optional[str]:
        try:
            return super().instructions
        except AttributeError:
            return self._instructions_override

    @instructions.setter
    def instructions(self, value: Optional[str]) -> None:
        self._instructions_override = value

    @property
    def stt(self):
        try:
            return super().stt
        except AttributeError:
            return self._stt_override

    @stt.setter
    def stt(self, value) -> None:
        self._stt_override = value

    @property
    def llm(self):
        try:
            return super().llm
        except AttributeError:
            return self._llm_override

    @llm.setter
    def llm(self, value) -> None:
        self._llm_override = value

    @property
    def tts(self):
        try:
            return super().tts
        except AttributeError:
            return self._tts_override

    @tts.setter
    def tts(self, value) -> None:
        self._tts_override = value

    @property
    def vad(self):
        try:
            return super().vad
        except AttributeError:
            return self._vad_override

    @vad.setter
    def vad(self, value) -> None:
        self._vad_override = value

    def __init__(
        self, 
        session_id: Optional[str] = None, 
        wandb_logger: Optional[Any] = None,
        backend_token: Optional[str] = None,
        user_email: Optional[str] = None,
        company: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.wandb_logger = wandb_logger
        self.user_email = user_email
        self.company = company
        
        # Use provided token or fall back to config token
        token = backend_token or CONFIG.backend.token
        self.backend = BackendClient(
            base_url=CONFIG.backend.url,
            token=token,
        )
        
        if backend_token:
            logger.info(
                f"🔐 VoiceAgent initialized with USER token for {user_email} @ {company}"
            )
        else:
            logger.warning("🔐 VoiceAgent initialized with SERVICE ACCOUNT token (fallback)")

        # Initialize RAG metrics reporter with wandb
        self.rag_metrics = RAGMetricsReporter(wandb_logger=wandb_logger)

        # Define tools BEFORE calling super().__init__()

        @function_tool
        async def query_info(query: str, top_k: int = 3) -> dict:
            """Answer policy/company questions via the RAG system."""
            start_time = time.time()
            logger.info(f"🔧 query_info tool called with query: {query}, top_k: {top_k}")

            await self.rag_metrics.on_rag_query_start(
                session_id=self.session_id,
                query=query,
                top_k=top_k
            )

            payload = {
                "query": query,
                "top_k": top_k,
                # Voice needs short spoken answers fast: cap generation length
                # and use Groq (Phase 1D/1E). Chat keeps the Mercury default.
                "max_tokens": 256,
                "generation_backend": "groq",
            }
            # Phase 2B/2C: consume the SSE endpoint with a 10s wall-clock
            # budget. collect_stream never raises, so the voice turn cannot
            # crash on stream failures.
            outcome = await self.backend.collect_stream(
                "/api/rag/query/stream", payload, total_timeout=10.0
            )

            answer_parts = []
            sources = []
            timings = {}

            for event in outcome["events"]:
                event_type = event["event"]
                event_data = event["data"] if isinstance(event["data"], dict) else {}

                if event_type == "sources":
                    sources = event_data.get("sources", [])
                elif event_type == "token":
                    answer_parts.append(event_data.get("token", ""))
                elif event_type == "done":
                    timings = event_data

            interrupted = (
                bool(outcome["error"]) or outcome["timed_out"] or not timings
            )

            if interrupted and not answer_parts:
                # Nothing salvageable from the stream: fall back to the batch
                # endpoint, which returns a graceful {"error": True, ...} dict
                # on final failure instead of raising.
                logger.warning(
                    "RAG stream yielded nothing "
                    f"(error={outcome['error']}, timed_out={outcome['timed_out']}); "
                    "falling back to batch endpoint"
                )
                data = await self.backend.post_with_retry(
                    "/api/rag/query", payload, timeout=8, max_retries=1
                )
            else:
                answer = "".join(answer_parts).strip()
                if interrupted:
                    # Phase 2C: partial answer, never cached server-side.
                    logger.warning(
                        f"RAG stream interrupted after {len(answer_parts)} tokens "
                        f"(error={outcome['error']}, timed_out={outcome['timed_out']})"
                    )
                    answer += (
                        " ... sorry, I lost my train of thought there. "
                        "Ask me again if you need the rest."
                    )
                data = {
                    "answer": answer,
                    "sources": sources,
                    "company": timings.get("company", self.company),
                    "duration_seconds": timings.get("duration_seconds", 0.0),
                    "retrieval_duration_seconds": timings.get(
                        "retrieval_duration_seconds", 0.0
                    ),
                    "generation_duration_seconds": timings.get(
                        "generation_duration_seconds", 0.0
                    ),
                    "cache_hit": False,
                }

            duration = time.time() - start_time

            if data.get("error"):
                await self.rag_metrics.on_rag_query_complete(
                    session_id=self.session_id,
                    query=query,
                    total_duration=duration,
                    backend_duration=0.0,
                    retrieval_duration=0.0,
                    generation_duration=0.0,
                    sources_count=0,
                    error=RuntimeError(data.get("detail", "tool call failed")),
                )
                logger.error(
                    f"❌ RAG query failed after {duration:.2f}s (graceful fallback returned)",
                    extra={
                        "session_id": self.session_id,
                        "query": query,
                        "error": data.get("detail"),
                        "metric_type": "rag_tool_call_error",
                    },
                )
                return {
                    "answer": data["answer"],
                    "sources": data.get("sources", []),
                    "error": True,
                }

            backend_duration = data.get("duration_seconds", 0.0)
            retrieval_duration = data.get("retrieval_duration_seconds", 0.0)
            generation_duration = data.get("generation_duration_seconds", 0.0)
            cache_hit = data.get("cache_hit", False)
            sources_count = len(data.get('sources', []))

            await self.rag_metrics.on_rag_query_complete(
                session_id=self.session_id,
                query=query,
                total_duration=duration,
                backend_duration=backend_duration,
                retrieval_duration=retrieval_duration,
                generation_duration=generation_duration,
                sources_count=sources_count,
                cache_hit=cache_hit
            )

            logger.info(
                f"✅ RAG query successful in {duration:.2f}s",
                extra={
                    "session_id": self.session_id,
                    "query": query,
                    "sources_count": sources_count,
                    "metric_type": "rag_tool_call"
                }
            )
            return {
                "answer": data["answer"],
                "sources": data["sources"],
                "company": data["company"],
            }

        @function_tool
        async def website_search(query: str) -> dict:
            """Search company website for public information."""
            logger.info(f"🔧 website_search tool called with query: {query}")
            payload = {"message": f"search the company website for: {query}"}
            resp = await self.backend.post_with_retry(
                "/api/chat/message", payload, timeout=10, max_retries=1
            )
            if resp.get("error"):
                logger.error(f"❌ Website search failed: {resp.get('detail')}")
                _voice_tool_inc("website_search", "error")
            else:
                logger.info("✅ Website search successful")
                _voice_tool_inc("website_search", "success")
            return resp

        @function_tool
        async def request_pto(message: str) -> dict:
            """Request PTO, check balance, or modify PTO requests."""
            resp = await self._agent_chat_stream("/api/pto/chat", message, "request_pto")
            _voice_tool_inc("request_pto", "error" if resp.get("error") else "success")
            return resp

        @function_tool
        async def create_hr_ticket(message: str) -> dict:
            """Escalate to HR for meetings, payroll, etc."""
            resp = await self._agent_chat_stream(
                "/api/hr-tickets/chat", message, "create_hr_ticket"
            )
            _voice_tool_inc("create_hr_ticket", "error" if resp.get("error") else "success")
            return resp

        agent_tools = [
            query_info,
            website_search,
            request_pto,
            create_hr_ticket,
        ]

        super().__init__(
            instructions="""
                You are FrontShiftAI, a voice-enabled assistant for deskless employees.
                Start by saying you are FrontShiftAI and introduce your capabilities briefly.
                Be friendly, concise, and helpful. 
                Keep responses brief and conversational since this is a voice chat.

                Your primary goal is to answer questions and help with simple workflows
                using the tools you have been given. You **must not hallucinate** company
                policies or HR information.

                You have these capabilities:

                1) `query_info` - use this for **all questions about company policies,
                    PTO rules, attendance, leaves, benefits, safety, HR guidelines,
                    onboarding, and any other handbook-like information
                    - Always call this tool FIRST when the user asks anything that might
                    involve internal company rules or policies.
                    - Use the returned `answer` and `sources` to ground your reply.

                2) `website_search` - use this when:
                    - `query_info` returns no useful sources, OR
                    - the user asks about general company info that is likely on the public website
                        (office locations, generic "about us", etc.).
                    If both RAG and website search fail, politely say you don't have enough information instead of guessing.

                3) `request_pto` - use this when the user wants to:
                    - request PTO or vacations
                    - check their PTO balance or history
                    - modify or cancel a PTO request

                4) `create_hr_ticket` - use this for:
                    - escalations about payroll, harassment, conflict, or complex HR issues
                    - when you cannot resolve something via policy and the user needs a human.

                General rules:
                - Prefer **tool calls over your own knowledge** whenever a tool is relevant.
                - For small talk or generic chit-chat ("how are you", "tell me a joke"),
                  you may respond directly without tools.
                - Always keep answers concise and spoken in a friendly, conversational tone.
                - Never invent policies or numbers; if tools don't give enough info,
                  say you don't know and, if appropriate, offer to create an HR ticket.

                Voice-specific guidelines (IMPORTANT):
                - When listing multiple items, speak naturally using transitions
                  like "first", "also", "additionally", "and finally" instead of "1", "2", "3"
                - Avoid reading like a teleprompter - speak as if explaining to a friend
                - Use natural pauses and flow, not bulleted lists
            """,
            tools=agent_tools
        )

        logger.info(f"✅ Agent initialized with {len(agent_tools)} tools")

        # Metric reporters
        stt_metrics = STTMetricsReporter(wandb_logger=wandb_logger)
        llm_metrics = LLMMetricsReporter(wandb_logger=wandb_logger)
        tts_metrics = TTSMetricsReporter(wandb_logger=wandb_logger)
        vad_metrics = VADMetricsReporter(wandb_logger=wandb_logger)

        def _schedule(coro, metric_name: str):
            async def _runner():
                try:
                    await coro
                except Exception:
                    logger.exception("Metrics handler failed for %s", metric_name)
            asyncio.create_task(_runner())

        def stt_wrapper(metrics: STTMetrics):
            _schedule(stt_metrics.on_stt_metrics_collected(metrics), "STT")

        def eou_wrapper(metrics: EOUMetrics):
            _schedule(stt_metrics.on_eou_metrics_collected(metrics), "EOU")

        def llm_wrapper(metrics: LLMMetrics):
            _schedule(llm_metrics.on_metrics_collected(metrics), "LLM")

        def tts_wrapper(metrics: TTSMetrics):
            _schedule(tts_metrics.on_metrics_collected(metrics), "TTS")

        def vad_wrapper(event: vad.VADEvent):
            _schedule(vad_metrics.on_vad_event(event), "VAD")

        self._stt_metrics_handler = stt_wrapper
        self._eou_metrics_handler = eou_wrapper
        self._llm_metrics_handler = llm_wrapper
        self._tts_metrics_handler = tts_wrapper
        self._vad_metrics_handler = vad_wrapper

    def update_backend_token(self, token: str) -> None:
        """Update the backend authentication token at runtime."""
        self.backend.update_token(token)

    async def _agent_chat_stream(self, path: str, message: str, tool: str) -> dict:
        """Phase 2D: call an agent chat endpoint via SSE, with batch fallback.

        Streams {path}/stream and returns the done event's body (same shape
        as the batch response). One idempotency key spans the stream attempt
        and the batch fallback, so a stream that already committed the
        mutation dedupes the retry server-side instead of double-applying it.
        """
        idempotency_key = str(uuid.uuid4())
        outcome = await self.backend.collect_stream(
            f"{path}/stream",
            {"message": message},
            total_timeout=15.0,
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        for event in outcome["events"]:
            data = event["data"] if isinstance(event["data"], dict) else {}
            if event["event"] == "status":
                logger.info(f"🧭 {tool} stage: {data.get('stage')}")
            elif event["event"] == "done":
                return data

        logger.warning(
            f"{tool} stream did not complete "
            f"(error={outcome['error']}, timed_out={outcome['timed_out']}); "
            "falling back to batch POST with the same idempotency key"
        )
        return await self.backend.post_with_retry(
            path,
            {"message": message},
            timeout=10,
            max_retries=1,
            idempotency_key=idempotency_key,
        )

    def attach_metric_sources(self, stt_chain, llm_chain, tts_chain, vad_monitor) -> None:
        """Register metric handlers against actual media components."""
        self.stt = stt_chain
        self.llm = llm_chain
        self.tts = tts_chain
        self.vad = vad_monitor

        if hasattr(self.stt, "on"):
            self.stt.on("metrics_collected", self._stt_metrics_handler)
            self.stt.on("metrics_collected", self._eou_metrics_handler)
        if hasattr(self.llm, "on"):
            self.llm.on("metrics_collected", self._llm_metrics_handler)
        if hasattr(self.tts, "on"):
            self.tts.on("metrics_collected", self._tts_metrics_handler)
        if hasattr(self.vad, "on"):
            self.vad.on("metrics_collected", self._vad_metrics_handler)


async def entrypoint(ctx: JobContext):
    """
    Main entrypoint for the voice agent.
    
    JWT Passthrough Flow:
    1. Connect to room
    2. Wait for user to join with their JWT in metadata
    3. Extract JWT and initialize agent with user's token
    4. Start voice session
    """
    session_id = os.getenv("VOICE_SESSION_ID") or uuid.uuid4().hex
    wandb_logger = WandbLogger(session_id=session_id)
    
    logger.info(f"🎯 Starting voice session: {session_id}")

    # Connect to the room
    await ctx.connect()
    logger.info(f"📡 Connected to room: {ctx.room.name}")

    # Wait for user with token to join (up to 15 seconds)
    user_metadata = await wait_for_user_token(ctx.room, max_wait=15.0)
    
    user_token = None
    user_email = None
    company = None
    
    if user_metadata:
        user_token = user_metadata.get("user_token")
        user_email = user_metadata.get("user_email")
        company = user_metadata.get("company")
        logger.info(f"🔐 User authenticated: {user_email} @ {company}")

    if not user_token:
        # Fail closed: refuse to start the session with a service-account token.
        # Backend calls would fail later with confusing auth errors; better to
        # apologize clearly now and exit.
        logger.error(
            "❌ No valid user token after metadata wait — aborting session to avoid running with service-account credentials"
        )
        try:
            tts_chain_tmp = _build_tts_chain(CONFIG.livekit.tts)
            session_tmp = AgentSession(tts=tts_chain_tmp)
            await session_tmp.start(agent=Agent(instructions=""), room=ctx.room)
            await session_tmp.say(
                "I'm sorry, I couldn't verify your account for this session. "
                "Please refresh the page and try again.",
                allow_interruptions=False,
            )
        except Exception:
            logger.exception("Failed to deliver auth-failure apology to user")
        return

    # Build provider chains
    stt_chain = _build_stt_chain(CONFIG.livekit.stt)
    llm_chain = _build_llm_chain(CONFIG.livekit.llm)
    tts_chain = _build_tts_chain(CONFIG.livekit.tts)
    vad_monitor = _build_vad(CONFIG.livekit.vad)

    # Create agent with user's token
    agent = VoiceAgent(
        session_id=session_id,
        wandb_logger=wandb_logger,
        backend_token=user_token,
        user_email=user_email,
        company=company,
    )
    agent.attach_metric_sources(stt_chain, llm_chain, tts_chain, vad_monitor)

    session = AgentSession(
        stt=stt_chain,
        llm=llm_chain,
        tts=tts_chain,
        vad=vad_monitor,
    )

    # Background audio (optional)
    background_audio = None
    if BACKGROUND_AUDIO_AVAILABLE:
        try:
            background_audio = BackgroundAudioPlayer(
                thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.3)],
            )
            logger.info("✅ BackgroundAudioPlayer initialized")
        except Exception as e:
            logger.warning(f"⚠️ BackgroundAudioPlayer failed: {e}")

    error_audio_path = Path(__file__).parent / "error_message.ogg"

    def _play_unavailable_prompt() -> None:
        audio_kwargs = {}
        if error_audio_path.exists():
            try:
                audio_kwargs["audio"] = audio_frames_from_file(str(error_audio_path))
            except Exception:
                pass
        session.say(
            "I'm having trouble connecting right now. Give me a second to reconnect",
            allow_interruptions=False,
            **audio_kwargs,
        )

    @session.on("error")
    def on_error(ev: ErrorEvent) -> None:
        err = getattr(ev, "error", None)
        source = getattr(ev, "source", None)
        recoverable = getattr(err, "recoverable", False)
        source_name = source.__class__.__name__ if source else "unknown"

        logger.error(f"Voice session error from {source_name}: {err}")

        # Phase 7B: classify error source for the voice_session_errors_total counter
        # so the resilience dashboard can attribute spikes to STT/TTS/LLM/LiveKit.
        if source and _safe_isinstance(source, tts.TTS):
            _voice_error_inc("tts")
        elif source and _safe_isinstance(source, llm.LLM):
            _voice_error_inc("llm")
        elif _is_stt_source(source):
            _voice_error_inc("stt")
        else:
            _voice_error_inc("livekit")

        if recoverable:
            return

        if source and _safe_isinstance(source, (tts.TTS, llm.LLM)):
            if hasattr(err, "recoverable"):
                err.recoverable = True
            session.say("I hit a small technical issue, but I'm back now.", allow_interruptions=True)
            return

        if _is_stt_source(source):
            try:
                session.update_agent(session.current_agent)
                if hasattr(err, "recoverable"):
                    err.recoverable = True
            except Exception:
                logger.exception("Failed to restart STT")
            return

        _play_unavailable_prompt()

    heartbeat_task: Optional[_asyncio.Task] = None
    cleanup_task: Optional[_asyncio.Task] = None

    async def _cleanup_resources() -> None:
        nonlocal heartbeat_task

        task = heartbeat_task
        heartbeat_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Worker heartbeat task failed")

        try:
            await agent.backend.close()
        except Exception:
            logger.exception("Failed to close backend HTTP client")

        if background_audio:
            try:
                await background_audio.aclose()
            except Exception:
                logger.exception("Failed to close background audio")

        if wandb_logger:
            try:
                wandb_logger.finish()
            except Exception:
                logger.exception("Failed to finish W&B logger")

    def _ensure_cleanup_task() -> _asyncio.Task:
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = _asyncio.create_task(_cleanup_resources())
        return cleanup_task

    async def _shutdown_cleanup() -> None:
        await _ensure_cleanup_task()

    add_shutdown_callback = getattr(ctx, "add_shutdown_callback", None)
    if callable(add_shutdown_callback):
        add_shutdown_callback(_shutdown_cleanup)

    @session.on("close")
    def on_close(ev: CloseEvent) -> None:
        logger.info(f"Voice session closing: {ev.reason}")

        # Phase 7B: decrement the active-session gauge as the session winds down.
        if _PROM_OK and voice_session_active is not None:
            try:
                voice_session_active.dec()
            except Exception:  # noqa: BLE001
                pass

        _ensure_cleanup_task()

        shutdown_job = getattr(ctx, "shutdown", None)
        if callable(shutdown_job):
            try:
                shutdown_job(reason=f"Voice session closed: {ev.reason}")
            except Exception:
                logger.exception("Failed to request LiveKit job shutdown")

    heartbeat_task = _asyncio.create_task(_emit_worker_heartbeat())
    try:
        # Phase 7B: count the session as active before start() so a hung
        # start() still shows up on the dashboard rather than vanishing.
        if _PROM_OK and voice_session_active is not None:
            try:
                voice_session_active.inc()
            except Exception:  # noqa: BLE001
                pass
        await session.start(agent=agent, room=ctx.room)
    except Exception:
        logger.exception("Failed to start VoiceAgent session")
        if _PROM_OK and voice_session_active is not None:
            try:
                voice_session_active.dec()
            except Exception:  # noqa: BLE001
                pass
        _voice_error_inc("livekit")
        await _ensure_cleanup_task()

        shutdown_job = getattr(ctx, "shutdown", None)
        if callable(shutdown_job):
            try:
                shutdown_job(reason="Voice session failed to start")
            except Exception:
                logger.exception("Failed to request LiveKit job shutdown")
        return

    # Generate greeting
    try:
        await session.generate_reply(
            instructions="Greet the user warmly and ask how you can help"
        )
        logger.info("✅ Initial greeting generated")
    except Exception:
        logger.exception("Failed to generate initial greeting")

    # Start background audio after greeting
    if background_audio:
        try:
            await background_audio.start(room=ctx.room, agent_session=session)
        except Exception as e:
            logger.warning(f"⚠️ Background audio start failed: {e}")


def handle_asyncio_exception(loop: _asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
    logger.exception("Unhandled asyncio error: %s", context)


try:
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_asyncio_exception)
except RuntimeError:
    pass


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
