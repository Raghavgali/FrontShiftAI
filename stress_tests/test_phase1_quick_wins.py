"""
Phase 1 stress tests: Quick Wins — Latency + Resilience.

Covers:
- 1A: HTTP connection pooling in BackendClient (pooled vs per-request clients)
- 1B: VAD tuned for lower end-of-turn latency (and valid silero kwargs)
- 1D: max_tokens honored per-request
- 1E: generation_backend honored per-request (groq for voice)
- Combined: all Phase 1 changes together hit latency targets

Run:
    STRESS_TEST_JWT=<token> pytest stress_tests/test_phase1_quick_wins.py -v -s
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import httpx
import pytest
import yaml

from conftest import LatencyReport

REPO_ROOT = Path(__file__).resolve().parents[1]
VOICE_CONFIG_PATH = REPO_ROOT / "voice_pipeline" / "configs" / "default.yaml"

# Valid keyword arguments of livekit.plugins.silero.VAD.load (durations are
# plain seconds — there are no *_ms parameters).
SILERO_VAD_LOAD_PARAMS = {
    "min_speech_duration",
    "min_silence_duration",
    "prefix_padding_duration",
    "max_buffered_speech",
    "activation_threshold",
    "deactivation_threshold",
    "padding_duration",
    "sample_rate",
    "force_cpu",
    "onnx_file_path",
}

RAG_PATH = "/api/rag/query"


# --------------------------------------------------------------------------- #
# 1A. HTTP connection pooling
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_http_pooling_vs_no_pooling(backend_url, auth_headers):
    """Pooled client should be measurably faster than per-request clients."""
    ITERATIONS = 30
    payload = {"query": "What is the PTO policy?", "top_k": 3}

    no_pool = LatencyReport("No Pooling", target_p50=1.5, target_p95=3.0)
    for _ in range(ITERATIONS):
        start = time.time()
        async with httpx.AsyncClient(
            base_url=backend_url, headers=auth_headers, timeout=15
        ) as c:
            await c.post(RAG_PATH, json=payload)
        no_pool.record(time.time() - start)

    pooled = LatencyReport("Pooled", target_p50=1.0, target_p95=2.5)
    async with httpx.AsyncClient(
        base_url=backend_url,
        headers=auth_headers,
        timeout=15,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    ) as client:
        for _ in range(ITERATIONS):
            start = time.time()
            await client.post(RAG_PATH, json=payload)
            pooled.record(time.time() - start)

    no_pool.report()
    pooled.report()
    improvement = statistics.mean(no_pool.times) - statistics.mean(pooled.times)
    assert improvement > 0.02, (
        f"Pooling should save at least 20ms, saved {improvement * 1000:.0f}ms"
    )


# --------------------------------------------------------------------------- #
# 1B. VAD tuning
# --------------------------------------------------------------------------- #
def test_vad_config_loaded():
    """VAD configured with aggressive thresholds and valid silero kwargs."""
    with open(VOICE_CONFIG_PATH) as f:
        data = yaml.safe_load(f)

    vad = data.get("livekit", {}).get("vad", {})
    assert vad.get("provider") == "silero"

    kwargs = vad.get("kwargs") or {}
    unknown = set(kwargs) - SILERO_VAD_LOAD_PARAMS
    assert not unknown, (
        f"VAD kwargs {unknown} are not silero.VAD.load parameters "
        f"(durations are seconds, e.g. min_silence_duration: 0.3, not *_ms)"
    )
    assert kwargs.get("min_silence_duration", 0.55) <= 0.3
    assert kwargs.get("min_speech_duration", 0.05) <= 0.1


# --------------------------------------------------------------------------- #
# 1D. max_tokens per request
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_max_tokens_accepted(http_client):
    """Requests with max_tokens should succeed and return an answer."""
    resp = await http_client.post(
        RAG_PATH,
        json={"query": "What is the PTO policy?", "top_k": 3, "max_tokens": 256},
    )
    resp.raise_for_status()
    body = resp.json()
    assert body.get("answer")


# --------------------------------------------------------------------------- #
# 1E. generation_backend per request
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_generation_backend_respected(http_client):
    """Requesting groq should be honored (unless groq fails and falls back)."""
    resp = await http_client.post(
        RAG_PATH,
        json={
            "query": "How many vacation days do employees get?",
            "top_k": 3,
            "generation_backend": "groq",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    assert body.get("answer")
    # Cache hits do not re-run generation, so only assert on fresh generations.
    if not body.get("cache_hit"):
        assert body.get("generation_backend") == "groq", (
            f"expected groq, got {body.get('generation_backend')} "
            "(groq may have failed and fallen back — check GROQ_API_KEY)"
        )


@pytest.mark.asyncio
async def test_groq_vs_mercury_generation(http_client):
    """Groq should be significantly faster than Mercury."""
    ITERATIONS = 20
    query = "What holidays does the company observe?"
    mercury = LatencyReport("Mercury", target_p50=2.0, target_p95=3.0)
    groq = LatencyReport("Groq", target_p50=1.0, target_p95=2.0)

    for _ in range(ITERATIONS):
        start = time.time()
        r = await http_client.post(
            RAG_PATH,
            json={"query": query, "top_k": 3, "generation_backend": "mercury"},
        )
        mercury.record(r.json().get("generation_duration_seconds", time.time() - start))

        start = time.time()
        r = await http_client.post(
            RAG_PATH,
            json={"query": query, "top_k": 3, "generation_backend": "groq"},
        )
        groq.record(r.json().get("generation_duration_seconds", time.time() - start))

    mercury.report()
    groq.report()
    ratio = statistics.mean(mercury.times) / max(statistics.mean(groq.times), 0.001)
    assert ratio > 1.3, f"Groq should be >1.3x faster than Mercury, got {ratio:.2f}x"


# --------------------------------------------------------------------------- #
# Combined
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_phase1_combined(http_client):
    """All Phase 1 changes combined should hit latency targets."""
    ITERATIONS = 30
    report = LatencyReport("Phase 1 Combined", target_p50=1.0, target_p95=1.8)
    for _ in range(ITERATIONS):
        start = time.time()
        r = await http_client.post(
            RAG_PATH,
            json={
                "query": "How many sick days do I get?",
                "top_k": 3,
                "max_tokens": 256,
                "generation_backend": "groq",
            },
        )
        r.raise_for_status()
        report.record(time.time() - start)
    report.assert_targets()
