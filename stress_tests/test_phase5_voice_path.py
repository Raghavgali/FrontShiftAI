"""
Phase 5 stress tests: Voice Fast Path + Voice Pipeline Resilience.

Covers:
- 5A: voice_prompt template exists, is TTS-safe, and template_key threads
  schema -> pipeline -> generator (the voice agent sends it)
- 5B: POST /api/rag/prefetch does retrieval only, and the warmed retrieval is
  actually reused by the following query; the voice-side scheduler debounces
  partial transcripts and is cancel-safe
- 5C: session reconnect backoff ladder (1s/2s/4s, max 3), the spoken
  "session ended unexpectedly" give-up path, and the Phase 3B supervision
  contract (WORKER_HEARTBEAT + consolidated idempotent cleanup) still intact
- 5D: metrics fall back to metrics.jsonl when W&B is unavailable, and the
  unavailability is logged at ERROR
- 5E: the keyword fallback used when LLM intent detection fails routes PTO
  statements to the PTO agent instead of opening an HR ticket

The tests that need a live deployment take the ``http_client`` fixture and are
skipped unless STRESS_TEST_JWT is set. Everything else runs locally, offline.

torch is broken in this checkout (missing libtorch_cpu.dylib), so the
torch-dependent retriever/reranker modules are stubbed in sys.modules before
chat_pipeline is imported. That is the established pattern for this repo.

Run:
    backend/backend_venv/bin/pytest stress_tests/test_phase5_voice_path.py -v
    STRESS_TEST_JWT=<token> backend/backend_venv/bin/pytest stress_tests/test_phase5_voice_path.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import sys
import time
import types
from pathlib import Path

import pytest

from conftest import LatencyReport

REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_MAIN = REPO_ROOT / "voice_pipeline" / "scripts" / "main.py"
MODAL_DEPLOY = REPO_ROOT / "voice_pipeline" / "modal_deploy.py"
BACKEND_RAG_API = REPO_ROOT / "backend" / "api" / "rag.py"

# Keep the production W&B monitor from opening a real run when backend
# modules are imported below.
os.environ.setdefault("WANDB_MODE", "disabled")

for path in (str(REPO_ROOT), str(REPO_ROOT / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)


# --------------------------------------------------------------------------- #
# torch-free stubs for the retrieval layer
# --------------------------------------------------------------------------- #
RETRIEVAL_CALLS: list = []


def _install_retrieval_stubs() -> None:
    """Inject fake retriever/reranker modules (torch is unusable locally)."""
    if "chat_pipeline.rag.retriever" in sys.modules and hasattr(
        sys.modules["chat_pipeline.rag.retriever"], "_phase5_stub"
    ):
        return

    retriever = types.ModuleType("chat_pipeline.rag.retriever")
    retriever._phase5_stub = True  # type: ignore[attr-defined]

    def vector_retrieval(query=None, top_k=5, company_name=None, max_documents=None):
        RETRIEVAL_CALLS.append(
            {"query": query, "top_k": top_k, "company_name": company_name}
        )
        docs = [f"policy chunk {i} for {company_name}" for i in range(2)]
        metadata = [
            {
                "company": company_name or "acme",
                "filename": "handbook.pdf",
                "chunk_id": i,
                "text": docs[i],
            }
            for i in range(2)
        ]
        return docs, metadata

    def bm25_retrieval(**kwargs):
        return vector_retrieval(**kwargs)

    retriever.vector_retrieval = vector_retrieval  # type: ignore[attr-defined]
    retriever.bm25_retrieval = bm25_retrieval  # type: ignore[attr-defined]
    sys.modules["chat_pipeline.rag.retriever"] = retriever

    reranker = types.ModuleType("chat_pipeline.rag.reranker")
    reranker._phase5_stub = True  # type: ignore[attr-defined]
    reranker.two_stage_reranker = lambda **kwargs: []  # type: ignore[attr-defined]
    sys.modules["chat_pipeline.rag.reranker"] = reranker


_install_retrieval_stubs()


@pytest.fixture
def pipeline_module():
    import chat_pipeline.rag.pipeline as module

    RETRIEVAL_CALLS.clear()
    return module


@pytest.fixture
def fake_pipeline(pipeline_module, monkeypatch):
    """A RAGPipeline whose generation step is recorded instead of executed."""
    generation_calls: list = []

    def fake_generation(**kwargs):
        generation_calls.append(kwargs)
        return "A short spoken answer.", kwargs.get("metadatas") or []

    monkeypatch.setattr(pipeline_module, "generation", fake_generation)
    monkeypatch.setattr(pipeline_module, "get_last_backend_used", lambda: "groq")

    pipeline = pipeline_module.RAGPipeline()
    pipeline.generation_calls = generation_calls  # type: ignore[attr-defined]
    return pipeline


# ========================================================================== #
# 5A. Voice prompt template
# ========================================================================== #
MARKDOWN_CHARS = ("*", "#", "`", "|", "~", "[", "]")


def test_voice_prompt_template_exists():
    from chat_pipeline.rag.prompt_templates import prompt_templates

    assert "voice_prompt" in prompt_templates, (
        "voice_prompt template missing; the voice agent sends "
        "template_key='voice_prompt'"
    )


def test_voice_prompt_is_tts_safe_and_short():
    """No markdown syntax, and short enough to keep spoken answers brief."""
    from chat_pipeline.rag.prompt_templates import prompt_templates

    template = prompt_templates["voice_prompt"]

    for char in MARKDOWN_CHARS:
        assert char not in template, (
            f"voice_prompt contains markdown character {char!r}; TTS reads it aloud"
        )
    for line in template.splitlines():
        assert not line.strip().startswith("-"), (
            f"voice_prompt contains a bullet line: {line!r}"
        )

    default = prompt_templates["general_prompt_1"]
    assert len(template) < len(default) * 2, "voice_prompt is unexpectedly long"
    assert "sentence" in template.lower(), (
        "voice_prompt should cap the answer length in sentences"
    )
    assert "markdown" in template.lower(), (
        "voice_prompt should explicitly forbid markdown"
    )


def test_voice_prompt_is_shorter_than_default_prompt():
    from chat_pipeline.rag.prompt_templates import prompt_templates

    assert len(prompt_templates["voice_prompt"]) < len(
        prompt_templates["general_prompt_1"]
    )


def test_generator_selects_voice_prompt_by_template_key():
    from chat_pipeline.rag.generator import _select_prompt_template
    from chat_pipeline.rag.prompt_templates import prompt_templates

    assert _select_prompt_template("voice_prompt") == (
        prompt_templates["voice_prompt"].strip()
    )
    # An unknown key must still fall back rather than blow up a live call.
    assert _select_prompt_template("no_such_template") == (
        prompt_templates["general_prompt_1"].strip()
    )


def test_request_schema_accepts_template_key():
    from schemas import RAGQueryRequest

    assert RAGQueryRequest(query="x").template_key is None
    assert (
        RAGQueryRequest(query="x", template_key="voice_prompt").template_key
        == "voice_prompt"
    )


def test_template_key_threads_pipeline_to_generator(fake_pipeline):
    """The pipeline must hand template_key down to the generator."""
    fake_pipeline.run(
        "What is the PTO policy?",
        company_name="acme",
        top_k=3,
        template_key="voice_prompt",
        streaming_overrides={"max_tokens": 256},
        generation_backend="groq",
    )

    assert fake_pipeline.generation_calls, "generation was never called"
    call = fake_pipeline.generation_calls[-1]
    assert call["template_key"] == "voice_prompt"
    assert call["generation_backend"] == "groq"
    assert call["streaming_overrides"]["max_tokens"] == 256


def test_rag_api_passes_template_key_on_both_endpoints():
    """Static check: both /query and /query/stream forward template_key."""
    source = BACKEND_RAG_API.read_text()
    assert source.count("template_key=request.template_key") >= 2, (
        "template_key must be forwarded from both the batch and stream endpoints"
    )


def test_voice_agent_requests_the_voice_template():
    source = VOICE_MAIN.read_text()
    assert '"template_key": "voice_prompt"' in source, (
        "voice agent must send template_key='voice_prompt' for spoken answers"
    )


# ========================================================================== #
# 5B. Prefetch on partial STT
# ========================================================================== #
def test_prefetch_endpoint_is_registered():
    import api.rag as rag_api

    routes = {
        route.path: route for route in rag_api.router.routes
        if hasattr(route, "methods")
    }
    assert "/api/rag/prefetch" in routes, "POST /api/rag/prefetch is not registered"
    assert "POST" in routes["/api/rag/prefetch"].methods


def test_prefetch_response_contract():
    from schemas import RAGPrefetchRequest, RAGPrefetchResponse

    assert RAGPrefetchRequest(query="pto?").top_k == 5
    fields = set(RAGPrefetchResponse.model_fields)
    assert {"query", "company", "cached", "documents"} <= fields


def test_prefetch_does_retrieval_only(fake_pipeline):
    """Prefetch must not generate: that is the whole point of the endpoint."""
    result = fake_pipeline.prefetch("what is the pto policy", "acme", top_k=3)

    assert result["cached"] is True
    assert result["documents"] == 2
    assert len(RETRIEVAL_CALLS) == 1
    assert fake_pipeline.generation_calls == [], (
        "prefetch generated an answer; it must be retrieval only"
    )


def test_prefetch_is_reused_by_the_following_query(fake_pipeline):
    """The real turn must reuse the warmed retrieval instead of redoing it."""
    fake_pipeline.prefetch("whats the pto policy", "acme", top_k=3)
    assert len(RETRIEVAL_CALLS) == 1

    # The final transcript differs in casing and punctuation, as STT finals do.
    result = fake_pipeline.run(
        "Whats the PTO policy?",
        company_name="acme",
        top_k=3,
        template_key="voice_prompt",
        streaming_overrides={"max_tokens": 256},
    )

    assert len(RETRIEVAL_CALLS) == 1, (
        "query re-ran retrieval instead of reusing the prefetch"
    )
    assert result.timings.get("retrieval_cache_hit") == 1.0
    assert fake_pipeline.generation_calls, "the query still has to generate"


def test_cold_query_reports_a_retrieval_cache_miss(fake_pipeline):
    result = fake_pipeline.run("a query nobody prefetched", company_name="acme", top_k=3)
    assert result.timings.get("retrieval_cache_hit") == 0.0
    assert len(RETRIEVAL_CALLS) == 1


def test_prefetch_cache_is_tenant_scoped(fake_pipeline):
    """A warm entry must never be served across companies."""
    fake_pipeline.prefetch("what is the pto policy", "acme", top_k=3)
    fake_pipeline.prefetch("what is the pto policy", "globex", top_k=3)

    assert len(RETRIEVAL_CALLS) == 2, "tenant B was served tenant A's retrieval"
    assert RETRIEVAL_CALLS[0]["company_name"] == "acme"
    assert RETRIEVAL_CALLS[1]["company_name"] == "globex"


def test_prefetch_cache_respects_top_k(fake_pipeline):
    fake_pipeline.prefetch("what is the pto policy", "acme", top_k=3)
    fake_pipeline.prefetch("what is the pto policy", "acme", top_k=8)
    assert len(RETRIEVAL_CALLS) == 2, "different top_k must not share an entry"


def test_prefetch_cache_expires(fake_pipeline, monkeypatch):
    """A stale warm entry must not be served forever after a re-index."""
    fake_pipeline.retrieval_cache_ttl = 0.01
    fake_pipeline.prefetch("what is the pto policy", "acme", top_k=3)
    assert len(RETRIEVAL_CALLS) == 1

    time.sleep(0.02)
    fake_pipeline.run("what is the pto policy", company_name="acme", top_k=3)
    assert len(RETRIEVAL_CALLS) == 2, "expired retrieval cache entry was reused"


def test_retrieval_query_normalization():
    from chat_pipeline.rag.pipeline import normalize_retrieval_query

    assert normalize_retrieval_query("  What's the PTO   policy? ") == (
        "what's the pto policy"
    )
    assert normalize_retrieval_query("Sick day tomorrow.") == "sick day tomorrow"
    assert normalize_retrieval_query("") == ""


# ---- voice-side scheduler -------------------------------------------------- #
def _scheduler(send, **kwargs):
    from voice_pipeline.utils.prefetch import PrefetchScheduler

    kwargs.setdefault("debounce_seconds", 0.0)
    return PrefetchScheduler(send, **kwargs)


@pytest.mark.asyncio
async def test_prefetch_scheduler_ignores_short_partials():
    async def scenario():
        sent = []

        async def send(query):
            sent.append(query)

        scheduler = _scheduler(send)
        assert scheduler.on_partial("what") is False
        assert scheduler.on_partial("how do i") is False
        await asyncio.sleep(0.01)
        assert sent == [], "short interim fragments must not hit the backend"
        assert scheduler.skipped_count == 2

    await scenario()


@pytest.mark.asyncio
async def test_prefetch_scheduler_debounces_repeated_partials():
    async def scenario():
        sent = []

        async def send(query):
            sent.append(query)

        scheduler = _scheduler(send)
        assert scheduler.on_partial("what is the pto policy") is True
        # Same words re-transcribed, then a one-word extension: neither is
        # worth another backend call.
        assert scheduler.on_partial("What is the PTO policy") is False
        assert scheduler.on_partial("what is the pto policy for") is False
        # A real change does earn one.
        assert scheduler.on_partial("what is the pto policy for new hires") is True

        await asyncio.sleep(0.02)
        assert scheduler.scheduled_count == 2
        assert len(sent) <= 2

    await scenario()


@pytest.mark.asyncio
async def test_prefetch_scheduler_cancels_the_superseded_partial():
    """A newer partial must cancel the pending one, not queue behind it."""

    async def scenario():
        sent = []

        async def send(query):
            sent.append(query)

        scheduler = _scheduler(send, debounce_seconds=0.05)
        scheduler.on_partial("what is the pto policy")
        await asyncio.sleep(0.01)  # still inside the debounce window
        scheduler.on_partial("what is the parental leave policy instead")
        await asyncio.sleep(0.12)

        assert sent == ["what is the parental leave policy instead"], (
            f"expected only the newest partial to be sent, got {sent}"
        )

    await scenario()


@pytest.mark.asyncio
async def test_prefetch_scheduler_swallows_backend_failures():
    """A prefetch failure must never surface into the voice turn."""

    async def scenario():
        async def send(query):
            raise RuntimeError("backend unreachable")

        scheduler = _scheduler(send)
        assert scheduler.on_partial("what is the pto policy") is True
        await asyncio.sleep(0.02)
        assert scheduler.dispatched_count == 0
        await scheduler.aclose()

    await scenario()


@pytest.mark.asyncio
async def test_prefetch_scheduler_never_blocks_the_caller():
    """on_partial returns immediately even when send() hangs."""

    async def scenario():
        started = asyncio.Event()

        async def slow_send(query):
            started.set()
            await asyncio.sleep(5)

        scheduler = _scheduler(slow_send)
        begin = time.perf_counter()
        scheduler.on_partial("what is the pto policy")
        elapsed = time.perf_counter() - begin

        assert elapsed < 0.05, f"on_partial blocked for {elapsed:.3f}s"
        await asyncio.sleep(0.01)
        assert started.is_set(), "the prefetch never started on its own task"
        await scheduler.aclose()

    await scenario()


def test_prefetch_scheduler_without_event_loop_is_a_noop():
    async def send(query):  # pragma: no cover - must never run
        raise AssertionError("should not be called without a loop")

    scheduler = _scheduler(send)
    assert scheduler.on_partial("what is the pto policy") is False


def test_voice_main_prefetches_on_partial_transcripts():
    source = VOICE_MAIN.read_text()
    assert "PrefetchScheduler" in source
    assert "user_input_transcribed" in source, (
        "no subscription to partial transcripts"
    )
    assert "is_final" in source, "partial and final transcripts are not distinguished"
    assert "/api/rag/prefetch" in source, "voice agent never calls the prefetch endpoint"
    assert "prefetch_scheduler.aclose()" in source, (
        "pending prefetch is not cleaned up on session close"
    )


# ========================================================================== #
# 5C. WebSocket reconnect
# ========================================================================== #
def test_reconnect_backoff_schedule_is_1_2_4():
    from voice_pipeline.utils.reconnect import reconnect_delays

    assert reconnect_delays() == [1.0, 2.0, 4.0]
    assert reconnect_delays(max_attempts=0) == []
    assert reconnect_delays(max_attempts=4) == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_reconnect_sleeps_1_2_4_then_gives_up():
    """Every attempt fails: 3 tries on the 1/2/4 ladder, then the apology."""

    async def scenario():
        slept, attempts, spoken = [], [], []

        async def fake_sleep(delay):
            slept.append(delay)

        async def always_fail(attempt):
            attempts.append(attempt)
            raise ConnectionError("websocket closed")

        async def on_give_up():
            spoken.append("session ended unexpectedly")

        from voice_pipeline.utils.reconnect import reconnect_with_backoff

        recovered = await reconnect_with_backoff(
            always_fail, on_give_up=on_give_up, sleep=fake_sleep
        )

        assert recovered is False
        assert slept == [1.0, 2.0, 4.0]
        assert attempts == [1, 2, 3], "must stop after 3 attempts"
        assert spoken == ["session ended unexpectedly"]

    await scenario()


@pytest.mark.asyncio
async def test_reconnect_stops_as_soon_as_it_succeeds():
    async def scenario():
        slept, attempts, spoken = [], [], []

        async def fake_sleep(delay):
            slept.append(delay)

        async def fail_once(attempt):
            attempts.append(attempt)
            if attempt == 1:
                raise ConnectionError("websocket closed")

        async def on_give_up():
            spoken.append("gave up")

        from voice_pipeline.utils.reconnect import reconnect_with_backoff

        recovered = await reconnect_with_backoff(
            fail_once, on_give_up=on_give_up, sleep=fake_sleep
        )

        assert recovered is True
        assert slept == [1.0, 2.0], "should not sleep for attempts it never makes"
        assert attempts == [1, 2]
        assert spoken == [], "no apology should be spoken after a recovery"

    await scenario()


@pytest.mark.asyncio
async def test_reconnect_survives_a_failing_apology():
    """If TTS itself is broken, the give-up path must still return cleanly."""

    async def scenario():
        async def fake_sleep(delay):
            return None

        async def always_fail(attempt):
            raise ConnectionError("closed")

        async def broken_tts():
            raise RuntimeError("tts unavailable")

        from voice_pipeline.utils.reconnect import reconnect_with_backoff

        recovered = await reconnect_with_backoff(
            always_fail, on_give_up=broken_tts, sleep=fake_sleep
        )
        assert recovered is False

    await scenario()


@pytest.mark.asyncio
async def test_reconnect_does_not_swallow_cancellation():
    """Session teardown during a reconnect must win."""

    async def scenario():
        async def fake_sleep(delay):
            return None

        async def cancelled(attempt):
            raise asyncio.CancelledError()

        from voice_pipeline.utils.reconnect import reconnect_with_backoff

        with pytest.raises(asyncio.CancelledError):
            await reconnect_with_backoff(cancelled, sleep=fake_sleep)

    await scenario()


def test_voice_main_wires_reconnect_with_a_spoken_give_up():
    source = VOICE_MAIN.read_text()
    assert "reconnect_with_backoff" in source, "session error does not reconnect"
    assert "max_attempts=3" in source and "base_delay=1.0" in source
    assert "ended unexpectedly" in source, (
        "no spoken notice before exiting on final reconnect failure"
    )
    assert "_start_reconnect()" in source, "reconnect never triggered from on_error"


def test_reconnect_preserves_phase3b_supervision_contract():
    """Phase 3B: heartbeat emitter and consolidated cleanup must survive."""
    source = VOICE_MAIN.read_text()

    # Heartbeat for the Modal watchdog.
    assert "WORKER_HEARTBEAT" in source
    assert "_emit_worker_heartbeat" in source
    assert "heartbeat_task = _asyncio.create_task(_emit_worker_heartbeat())" in source

    # One idempotent cleanup task, registered on close and on job shutdown.
    assert "def _ensure_cleanup_task" in source
    assert "add_shutdown_callback(_shutdown_cleanup)" in source
    assert source.count("cleanup_task = _asyncio.create_task(_cleanup_resources())") == 1

    # The reconnect must not cancel the heartbeat: only cleanup may do that.
    reconnect_block = source.split("async def _run_reconnect")[1].split(
        "def _start_reconnect"
    )[0]
    assert "heartbeat" not in reconnect_block.lower(), (
        "reconnect touches the heartbeat task; that would trip the watchdog"
    )


def test_reconnect_window_fits_inside_the_watchdog_timeout():
    """Worst-case reconnect time must stay well under the heartbeat timeout."""
    from voice_pipeline.utils.reconnect import reconnect_delays

    worst_case = sum(reconnect_delays())
    modal_source = MODAL_DEPLOY.read_text()
    assert "heartbeat_timeout: float = 60.0" in modal_source, (
        "heartbeat timeout changed; re-check the reconnect budget"
    )
    assert worst_case < 60.0 / 2, (
        f"reconnect ladder ({worst_case}s) is too close to the 60s watchdog"
    )


# ========================================================================== #
# 5D. Metrics fallback to file
# ========================================================================== #
def test_metrics_sink_writes_json_lines(tmp_path):
    from voice_pipeline.utils.metrics_sink import FileMetricsSink

    sink = FileMetricsSink("session-abc", log_dir=tmp_path)
    assert sink.path == tmp_path / "session-abc" / "metrics.jsonl"

    assert sink.write({"llm/ttft": 0.42}, reason="wandb_disabled") is True
    assert sink.write({"tts/ttfb": 0.11}, reason="wandb_disabled") is True

    lines = sink.path.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["session_id"] == "session-abc"
    assert first["fallback_reason"] == "wandb_disabled"
    assert first["metrics"] == {"llm/ttft": 0.42}
    assert "timestamp" in first


def test_metrics_sink_survives_unserializable_payloads(tmp_path):
    from voice_pipeline.utils.metrics_sink import FileMetricsSink

    class Unserializable:
        def __repr__(self):
            return "<weird>"

    sink = FileMetricsSink("s", log_dir=tmp_path)
    assert sink.write({"obj": Unserializable()}) is True
    record = json.loads(sink.path.read_text().splitlines()[0])
    assert "weird" in json.dumps(record)


def test_metrics_sink_never_raises_on_unwritable_path(tmp_path):
    from voice_pipeline.utils.metrics_sink import FileMetricsSink

    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")

    sink = FileMetricsSink("s", log_dir=blocker)
    assert sink.write({"a": 1}) is False, "a broken path must not raise"
    assert sink.write_failures == 1


def test_metrics_sink_honours_log_dir_env(monkeypatch, tmp_path):
    from voice_pipeline.utils.metrics_sink import session_log_dir

    monkeypatch.setenv("VOICE_PIPELINE_LOG_DIR", str(tmp_path / "envdir"))
    assert session_log_dir("s1") == tmp_path / "envdir" / "s1"


def test_wandb_disabled_still_writes_metrics_to_file(tmp_path):
    from voice_pipeline.utils.wandb_logger import WandbLogger

    logger_obj = WandbLogger(session_id="s-disabled", enabled=False, log_dir=tmp_path)
    logger_obj.log_llm_metrics(duration=1.0, ttft=0.3)
    logger_obj.log_tts_metrics(duration=0.5, ttfb=0.2)
    logger_obj.log_rag_metrics(
        query="pto policy",
        total_duration=1.0,
        backend_duration=0.9,
        retrieval_duration=0.3,
        generation_duration=0.5,
        sources_count=2,
    )

    lines = logger_obj.file_sink.path.read_text().splitlines()
    assert len(lines) == 3, f"expected 3 metric lines, got {len(lines)}"
    payloads = [json.loads(line)["metrics"] for line in lines]
    assert "llm/ttft" in payloads[0]
    assert "tts/ttfb" in payloads[1]
    assert "rag/total_duration" in payloads[2]
    # Steps must keep advancing so the file mirrors what W&B would have seen.
    assert [json.loads(line)["step"] for line in lines] == [0, 1, 2]


def test_wandb_unavailable_is_logged_at_error_and_falls_back(tmp_path, monkeypatch, caplog):
    """A broken W&B must be reported at ERROR, not silently pass."""
    import voice_pipeline.utils.wandb_logger as wandb_logger_module

    class BrokenWandb:
        def login(self, **kwargs):
            return None

        def init(self, **kwargs):
            raise RuntimeError("wandb api unreachable")

        def log(self, *args, **kwargs):  # pragma: no cover - never reached
            raise RuntimeError("wandb api unreachable")

        def finish(self):
            return None

    monkeypatch.setattr(wandb_logger_module, "wandb", BrokenWandb())
    monkeypatch.setattr(wandb_logger_module, "WANDB_AVAILABLE", True)

    with caplog.at_level(logging.ERROR, logger=wandb_logger_module.__name__):
        logger_obj = wandb_logger_module.WandbLogger(
            session_id="s-broken", enabled=True, log_dir=tmp_path
        )
        logger_obj.log_stt_metrics(duration=0.3, audio_duration=1.0)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "W&B unavailability was not logged at ERROR"
    assert any("wandb" in r.getMessage().lower() for r in errors)

    lines = logger_obj.file_sink.path.read_text().splitlines()
    assert len(lines) == 1, "metrics were dropped instead of written to the file"
    record = json.loads(lines[0])
    assert record["fallback_reason"] == "wandb_init_failed"
    assert "stt/duration" in record["metrics"]


def test_wandb_failure_mid_session_keeps_the_call_alive(tmp_path, monkeypatch):
    """A W&B log() that starts failing must not raise into the voice turn."""
    import voice_pipeline.utils.wandb_logger as wandb_logger_module

    class FlakyWandb:
        def login(self, **kwargs):
            return None

        def init(self, **kwargs):
            return type("Run", (), {"id": "r1", "url": "http://x"})()

        def log(self, *args, **kwargs):
            raise RuntimeError("wandb dropped the connection")

        def finish(self):
            return None

    monkeypatch.setattr(wandb_logger_module, "wandb", FlakyWandb())
    monkeypatch.setattr(wandb_logger_module, "WANDB_AVAILABLE", True)

    logger_obj = wandb_logger_module.WandbLogger(
        session_id="s-flaky", enabled=True, log_dir=tmp_path
    )
    assert logger_obj.enabled is True

    logger_obj.log_llm_metrics(duration=1.0, ttft=0.2)  # must not raise
    logger_obj.log_llm_metrics(duration=1.1, ttft=0.3)

    lines = logger_obj.file_sink.path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["fallback_reason"] == "wandb_log_failed"


def test_modal_deploy_reports_missing_wandb_secret_and_sets_log_dir():
    source = MODAL_DEPLOY.read_text()
    assert "except Exception:\n    pass" not in source, (
        "the optional W&B secret is still swallowed silently"
    )
    assert "ERROR" in source and "wandb-credentials" in source
    assert 'worker_env["VOICE_PIPELINE_LOG_DIR"]' in source, (
        "worker has no writable directory for metrics.jsonl"
    )
    # Phase 3B wiring must be untouched.
    assert "run_supervised_process" in source
    assert "max_restarts" in source and "heartbeat_timeout" in source


# ========================================================================== #
# 5E. Intent detection fallback
# ========================================================================== #
@pytest.fixture
def detect_intent_with_broken_llm(monkeypatch):
    """detect_intent with LLM intent detection forced to fail."""
    import api.unified_agent as unified_agent

    def broken_llm_client():
        raise RuntimeError("all LLM providers failed")

    monkeypatch.setattr(unified_agent, "get_llm_client", broken_llm_client)
    return unified_agent.detect_intent


PTO_FALLBACK_MESSAGES = [
    "I need vacation",
    "time off next week",
    "request pto",
    "sick day tomorrow",
    "taking a personal day on friday",
    "vacation from the 3rd to the 9th",
    "put me down for parental leave",
    "i want to use my pto",
]


@pytest.mark.parametrize("message", PTO_FALLBACK_MESSAGES)
def test_intent_fallback_routes_pto_statements_to_pto(
    detect_intent_with_broken_llm, message
):
    result = detect_intent_with_broken_llm(message)
    assert result["agent"] == "pto", (
        f"'{message}' routed to {result['agent']} instead of pto"
    )


def test_intent_fallback_keeps_the_return_shape(detect_intent_with_broken_llm):
    result = detect_intent_with_broken_llm("sick day tomorrow")
    assert set(result) == {"agent", "confidence"}
    assert result["confidence"] in {"high", "medium", "low"}


def test_intent_fallback_still_sends_real_hr_requests_to_hr(
    detect_intent_with_broken_llm,
):
    for message in [
        "my paycheck was wrong again",
        "i had a conflict with my manager",
        "someone needs to look at my insurance enrollment",
    ]:
        result = detect_intent_with_broken_llm(message)
        assert result["agent"] == "hr_ticket", (
            f"'{message}' routed to {result['agent']} instead of hr_ticket"
        )


def test_intent_fallback_keeps_policy_questions_on_rag(detect_intent_with_broken_llm):
    """PTO keywords must not steal handbook questions from RAG."""
    for message in [
        "what is the vacation policy",
        "how does time off accrue",
        "tell me about the leave policy",
    ]:
        result = detect_intent_with_broken_llm(message)
        assert result["agent"] == "rag", (
            f"'{message}' routed to {result['agent']} instead of rag"
        )


def test_pto_fallback_keywords_are_exposed():
    from api.unified_agent import PTO_FALLBACK_KEYWORDS

    for keyword in ("vacation", "time off", "leave", "pto", "sick day"):
        assert keyword in PTO_FALLBACK_KEYWORDS, f"missing PTO keyword: {keyword}"


# ========================================================================== #
# Live deployment tests (skipped unless STRESS_TEST_JWT is set)
# ========================================================================== #
@pytest.mark.asyncio
async def test_prefetch_speed(http_client):
    """Prefetch (retrieval only) should be well under half a second."""
    report = LatencyReport("Prefetch", target_p50=0.3, target_p95=0.5)
    for _ in range(20):
        start = time.time()
        resp = await http_client.post(
            "/api/rag/prefetch", json={"query": "PTO policy?", "top_k": 3}
        )
        resp.raise_for_status()
        report.record(time.time() - start)
    report.assert_targets()


@pytest.mark.asyncio
async def test_prefetch_response_shape_live(http_client):
    resp = await http_client.post(
        "/api/rag/prefetch", json={"query": "What is the PTO policy?", "top_k": 3}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"query", "company", "cached", "documents"}
    assert "answer" not in body, "prefetch must not generate an answer"


@pytest.mark.asyncio
async def test_prefetch_is_faster_than_a_full_query(http_client):
    query = "What is the parental leave policy?"

    start = time.time()
    prefetch = await http_client.post(
        "/api/rag/prefetch", json={"query": query, "top_k": 3}
    )
    prefetch_seconds = time.time() - start
    prefetch.raise_for_status()

    start = time.time()
    full = await http_client.post(
        "/api/rag/query", json={"query": query, "top_k": 3, "max_tokens": 256}
    )
    query_seconds = time.time() - start
    full.raise_for_status()

    print(f"\n  prefetch: {prefetch_seconds:.3f}s | full query: {query_seconds:.3f}s")
    assert prefetch_seconds < query_seconds, (
        "retrieval-only prefetch was not faster than a full generate"
    )


@pytest.mark.asyncio
async def test_voice_prompt_gives_shorter_answers(http_client):
    """The voice template should produce shorter answers than the default."""
    query = "What are the company holidays?"
    default_lengths, voice_lengths = [], []

    for _ in range(5):
        resp = await http_client.post(
            "/api/rag/query", json={"query": query, "top_k": 3, "max_tokens": 1024}
        )
        resp.raise_for_status()
        default_lengths.append(len(resp.json()["answer"]))

        resp = await http_client.post(
            "/api/rag/query",
            json={
                "query": query,
                "top_k": 3,
                "max_tokens": 256,
                "template_key": "voice_prompt",
            },
        )
        resp.raise_for_status()
        voice_lengths.append(len(resp.json()["answer"]))

    print(
        f"\n  default mean: {statistics.mean(default_lengths):.0f} chars"
        f" | voice mean: {statistics.mean(voice_lengths):.0f} chars"
    )
    assert statistics.mean(voice_lengths) < statistics.mean(default_lengths)


@pytest.mark.asyncio
async def test_voice_answers_have_no_markdown(http_client):
    """Spoken answers must not contain markdown for the TTS to read out."""
    resp = await http_client.post(
        "/api/rag/query",
        json={
            "query": "What is the PTO policy?",
            "top_k": 3,
            "max_tokens": 256,
            "template_key": "voice_prompt",
        },
    )
    resp.raise_for_status()
    answer = resp.json()["answer"]
    for char in ("*", "#", "`"):
        assert char not in answer, f"voice answer contains markdown {char!r}: {answer!r}"


@pytest.mark.asyncio
async def test_prefetch_then_query_reports_a_warm_retrieval(http_client):
    """End to end: prefetch, then the query should retrieve faster."""
    query = f"What is the dress code policy? {time.time()}"

    cold = await http_client.post(
        "/api/rag/query", json={"query": query, "top_k": 3, "max_tokens": 256}
    )
    cold.raise_for_status()
    cold_retrieval = cold.json().get("retrieval_duration_seconds") or 0.0

    warm_query = f"{query}?"
    prefetch = await http_client.post(
        "/api/rag/prefetch", json={"query": warm_query, "top_k": 3}
    )
    prefetch.raise_for_status()

    warm = await http_client.post(
        "/api/rag/query", json={"query": warm_query, "top_k": 3, "max_tokens": 256}
    )
    warm.raise_for_status()
    warm_retrieval = warm.json().get("retrieval_duration_seconds") or 0.0

    print(f"\n  cold retrieval: {cold_retrieval:.4f}s | warm: {warm_retrieval:.4f}s")
    assert warm_retrieval <= cold_retrieval, (
        "prefetch did not make the follow-up retrieval any cheaper"
    )
