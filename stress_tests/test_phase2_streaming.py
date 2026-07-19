"""
Phase 2 stress tests: Streaming RAG endpoint + agent SSE streaming.

Covers:
- 2A: POST /api/rag/query/stream emits sources -> token* -> done
- 2B/2C: TTFT beats batch; interrupted streams never poison the cache
- 2D: PTO/HR agent stream endpoints emit per-node status events

Run:
    STRESS_TEST_JWT=<token> pytest stress_tests/test_phase2_streaming.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time

import pytest

from conftest import LatencyReport

RAG_STREAM = "/api/rag/query/stream"


async def iter_sse(resp):
    """Yield (event, data) pairs from an httpx streaming SSE response."""
    event = "message"
    data_lines = []
    async for line in resp.aiter_lines():
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
            continue
        if line == "" and data_lines:
            raw = "\n".join(data_lines)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
            yield event, data
            event = "message"
            data_lines = []


# --------------------------------------------------------------------------- #
# 2A. SSE contract
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rag_stream_event_contract(http_client):
    """Stream must emit sources first, then tokens, then a done event."""
    payload = {"query": "What is the PTO policy?", "top_k": 3, "max_tokens": 256}
    order = []
    done_data = None
    async with http_client.stream("POST", RAG_STREAM, json=payload) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for event, data in iter_sse(resp):
            order.append(event)
            if event == "done":
                done_data = data
                break

    assert order[0] == "sources", f"first event was {order[0]}"
    assert "token" in order, "no token events emitted"
    assert done_data is not None, "no done event emitted"
    assert "retrieval_duration_seconds" in done_data


# --------------------------------------------------------------------------- #
# 2B. TTFT vs batch
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_streaming_ttft(http_client):
    """Streaming should deliver first token faster than batch."""
    ITERATIONS = 20
    payload = {"query": "What is the bereavement leave policy?", "top_k": 3,
               "max_tokens": 256, "generation_backend": "groq"}

    batch = LatencyReport("Batch /query", target_p50=1.5, target_p95=2.5)
    stream = LatencyReport("Stream TTFT", target_p50=0.6, target_p95=1.2)

    for _ in range(ITERATIONS):
        start = time.time()
        await http_client.post("/api/rag/query", json=payload)
        batch.record(time.time() - start)

        start = time.time()
        async with http_client.stream("POST", RAG_STREAM, json=payload) as resp:
            async for event, _data in iter_sse(resp):
                if event == "token":
                    stream.record(time.time() - start)
                    break

    batch.report()
    stream.report()
    assert statistics.mean(batch.times) - statistics.mean(stream.times) > 0.2


# --------------------------------------------------------------------------- #
# 2C. Interruption safety
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_stream_interruption_handled(http_client):
    """Interrupted stream should not crash or cache partial results."""
    payload = {"query": "What benefits are offered?", "top_k": 3, "max_tokens": 256}

    # Read only the first 2 tokens then close the connection.
    async with http_client.stream("POST", RAG_STREAM, json=payload) as resp:
        count = 0
        async for event, _data in iter_sse(resp):
            if event == "token":
                count += 1
                if count >= 2:
                    break  # Simulate client disconnect

    # Next full query should NOT return a cached partial answer.
    r = await http_client.post("/api/rag/query", json=payload)
    r.raise_for_status()
    answer = r.json()["answer"]
    assert len(answer) > 50, "Answer seems truncated: partial result may have been cached"


@pytest.mark.asyncio
async def test_streaming_concurrent_sessions(http_client):
    """10 concurrent streaming sessions should all complete."""
    CONCURRENT = 10
    payload = {"query": "What benefits are offered?", "top_k": 3, "max_tokens": 256}

    async def stream_session():
        start = time.time()
        tokens = 0
        async with http_client.stream("POST", RAG_STREAM, json=payload) as resp:
            async for event, _data in iter_sse(resp):
                if event == "token":
                    tokens += 1
                if event == "done":
                    break
        return time.time() - start, tokens

    results = await asyncio.gather(*[stream_session() for _ in range(CONCURRENT)])
    times = [r[0] for r in results]
    assert max(times) < 5.0, f"Worst concurrent stream: {max(times):.1f}s"
    assert all(r[1] > 0 for r in results), "some sessions produced no tokens"


# --------------------------------------------------------------------------- #
# 2D. Agent streaming
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pto_stream_emits_node_status(http_client):
    """PTO streaming endpoint must emit status events for each LangGraph node."""
    stages = []
    done = None
    async with http_client.stream(
        "POST", "/api/pto/chat/stream", json={"message": "I need 3 days off next week"}
    ) as resp:
        assert resp.status_code == 200
        async for event, data in iter_sse(resp):
            if event == "status" and isinstance(data, dict):
                stages.append(data.get("stage"))
            if event in ("done", "error"):
                done = (event, data)
                break

    assert "parse_intent" in stages, f"stages seen: {stages}"
    assert "check_balance" in stages, f"stages seen: {stages}"
    assert done is not None and done[0] == "done", f"terminal event: {done}"
    assert done[1].get("response"), "done event has no response text"


@pytest.mark.asyncio
async def test_pto_stream_first_status_under_500ms(http_client):
    """First status event should arrive quickly (before full response)."""
    start = time.time()
    first_status_time = None
    async with http_client.stream(
        "POST", "/api/pto/chat/stream", json={"message": "check my PTO balance"}
    ) as resp:
        async for event, _data in iter_sse(resp):
            if event == "status":
                first_status_time = time.time() - start
                break
    assert first_status_time is not None and first_status_time < 0.5, (
        f"first status after {first_status_time}s"
    )


@pytest.mark.asyncio
async def test_hr_stream_emits_node_status(http_client):
    """HR ticket streaming endpoint must emit workflow status events."""
    stages = []
    done = None
    async with http_client.stream(
        "POST", "/api/hr-tickets/chat/stream",
        json={"message": "I have a question about my payroll deductions"},
    ) as resp:
        assert resp.status_code == 200
        async for event, data in iter_sse(resp):
            if event == "status" and isinstance(data, dict):
                stages.append(data.get("stage"))
            if event in ("done", "error"):
                done = (event, data)
                break

    assert "parse_intent" in stages, f"stages seen: {stages}"
    assert "generate_response" in stages, f"stages seen: {stages}"
    assert done is not None and done[0] == "done", f"terminal event: {done}"
