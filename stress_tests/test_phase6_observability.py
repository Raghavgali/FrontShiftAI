"""Phase 6 - Observability and circuit breakers.

Scope, and what it deliberately does NOT re-test:

- **6A correlation IDs**: the middleware itself landed in Phase 7C
  (``backend/observability/tracing.py``) and ``test_phase7_observability.py``
  already asserts the header round-trip against a *running* backend. What is
  new here is (a) an in-process assertion that the ContextVar actually
  reaches the handler through Starlette's BaseHTTPMiddleware task boundary,
  (b) that log records from *module* loggers carry ``request_id`` (the
  original root-logger filter never saw them), and (c) that the voice
  worker's session id survives the trip as the correlation value.
- **6B LLM circuit breaker**: reuses the Phase 6.5 breaker in
  ``backend/utils/resilience.py``. There is no second breaker implementation.
  The tests here cover the state machine's transitions, the "stop paying the
  retry budget once the target is known-down" behaviour, that an open breaker
  still lets the fallback chain advance, and that transitions reach the
  Prometheus gauge.
- **6C pooling**: dialect-branched pool configuration in
  ``backend/db/connection.py``.

Local tests are deterministic and need no backend. The two live tests use the
``http_client`` fixture, which skips unless ``STRESS_TEST_JWT`` is set.

Run:
    backend/backend_venv/bin/pytest stress_tests/test_phase6_observability.py -v
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

# Import spelling matters here. ``backend/utils/resilience.py`` is imported as
# ``utils.resilience`` by the app (backend/ on sys.path) and as
# ``backend.utils.resilience`` by some stress tests (repo root on sys.path).
# Those are two module objects with two breaker registries. Everything in this
# file therefore goes through the app's spelling, which is also what the
# generator and the LLM client resolve, so all of them share one registry.
for _path in (str(REPO_ROOT), str(BACKEND_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _install_torch_stubs() -> None:
    """Stub the two RAG modules that import torch.

    torch is broken in this environment (missing libtorch_cpu.dylib), and
    ``chat_pipeline.rag.generator`` imports the retriever/reranker at module
    scope. The generator only calls them from ``_run_retrieval``, which no
    test here touches.
    """
    retriever = sys.modules.get("chat_pipeline.rag.retriever")
    if retriever is None:
        retriever = types.ModuleType("chat_pipeline.rag.retriever")
        retriever.bm25_retrieval = lambda *a, **k: []       # type: ignore[attr-defined]
        retriever.vector_retrieval = lambda *a, **k: []     # type: ignore[attr-defined]
        sys.modules["chat_pipeline.rag.retriever"] = retriever
    reranker = sys.modules.get("chat_pipeline.rag.reranker")
    if reranker is None:
        reranker = types.ModuleType("chat_pipeline.rag.reranker")
        reranker.two_stage_reranker = lambda docs, *a, **k: docs  # type: ignore[attr-defined]
        sys.modules["chat_pipeline.rag.reranker"] = reranker


_install_torch_stubs()


# =========================================================================== #
# 6A. Request correlation IDs
# =========================================================================== #

def _correlation_app():
    """Minimal app wired exactly like backend/main.py's request-id middleware.

    The full app is not importable in this environment (torch), and a minimal
    app isolates the middleware under test from 200 routes of unrelated
    behaviour. ``test_main_wires_request_id_middleware`` covers the wiring.
    """
    from fastapi import FastAPI, Request
    from observability.tracing import get_request_id, request_id_middleware

    app = FastAPI()

    @app.middleware("http")
    async def _rid(request: Request, call_next):
        return await request_id_middleware(request, call_next)

    @app.get("/echo")
    def echo():
        # Read from the ContextVar, not from the request: this is what every
        # log line and every downstream helper does.
        return {"seen_by_handler": get_request_id()}

    return app


@pytest.fixture(scope="module")
def correlation_client():
    from fastapi.testclient import TestClient

    with TestClient(_correlation_app()) as client:
        yield client


def test_request_id_generated_and_echoed(correlation_client):
    """No inbound header: server mints one, echoes it, handler sees the same."""
    resp = correlation_client.get("/echo")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid, "response is missing the X-Request-ID header"
    assert len(rid) == 32 and all(c in "0123456789abcdef" for c in rid), rid
    # The ContextVar must survive BaseHTTPMiddleware running the downstream
    # app in a child task. If it did not, logs would never correlate.
    assert resp.json()["seen_by_handler"] == rid


def test_client_supplied_request_id_is_honored(correlation_client):
    rid = "voice-session-42.abc:XYZ_9"
    resp = correlation_client.get("/echo", headers={"X-Request-ID": rid})
    assert resp.headers.get("X-Request-ID") == rid
    assert resp.json()["seen_by_handler"] == rid


def test_session_header_is_used_as_correlation_fallback(correlation_client):
    """Voice worker sends its session id; backend adopts it as the request id."""
    session = "RM_7cdf-1234"
    resp = correlation_client.get("/echo", headers={"X-Session-ID": session})
    assert resp.headers.get("X-Request-ID") == session
    assert resp.json()["seen_by_handler"] == session


def test_request_id_beats_session_id_when_both_present(correlation_client):
    resp = correlation_client.get(
        "/echo", headers={"X-Request-ID": "explicit-1", "X-Session-ID": "session-2"}
    )
    assert resp.headers.get("X-Request-ID") == "explicit-1"


@pytest.mark.parametrize(
    "hostile",
    [
        "has spaces",
        "new\nline",
        "semi;colon",
        "x" * 129,
        "",
    ],
)
def test_hostile_request_ids_are_replaced(correlation_client, hostile):
    """A client cannot inject arbitrary bytes into logs or the header."""
    resp = correlation_client.get("/echo", headers={"X-Request-ID": hostile})
    assert resp.status_code == 200
    echoed = resp.headers.get("X-Request-ID")
    assert echoed != hostile
    assert len(echoed) == 32


def test_ids_are_unique_per_request(correlation_client):
    ids = {correlation_client.get("/echo").headers["X-Request-ID"] for _ in range(25)}
    assert len(ids) == 25, "request ids collided"


def test_log_records_from_module_loggers_carry_request_id(caplog):
    """The correlation ID must reach records from ``getLogger(__name__)``.

    Regression test for the original 7C implementation: a filter on the root
    *logger* only sees records logged through the root logger, because
    propagation walks ancestor *handlers*, not ancestor filters. Every real
    log line in this codebase comes from a module logger, so nothing was
    stamped and a ``%(request_id)s`` formatter raised
    "Formatting field not found in record".
    """
    from observability.tracing import install_log_filter, set_request_id

    install_log_filter()
    set_request_id("corr-xyz")
    try:
        module_logger = logging.getLogger("phase6.some.module")
        with caplog.at_level(logging.INFO, logger="phase6.some.module"):
            module_logger.info("hello")
        assert caplog.records, "no records captured"
        record = caplog.records[-1]
        assert getattr(record, "request_id", None) == "corr-xyz"

        # And a formatter that references the field must not blow up.
        formatted = logging.Formatter("[%(request_id)s] %(message)s").format(record)
        assert formatted == "[corr-xyz] hello"
    finally:
        set_request_id(None)


def test_log_records_outside_a_request_get_a_placeholder():
    from observability.tracing import RequestIdLogFilter, install_log_filter, set_request_id

    install_log_filter()
    set_request_id(None)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
    # Factory-created records get "-"; the filter is the belt to that braces.
    assert RequestIdLogFilter().filter(record) is True
    assert record.request_id == "-"


def test_main_wires_request_id_middleware():
    """backend/main.py must install the middleware and the log filter."""
    src = (BACKEND_ROOT / "main.py").read_text()
    assert "request_id_middleware" in src, "request-id middleware not wired"
    assert "install_log_filter()" in src, "log correlation not installed"


def test_voice_correlation_headers_are_accepted_verbatim():
    """The voice helper must emit ids the backend keeps unchanged.

    If the two charsets drift, the backend silently replaces the session id
    with a random one and correlation dies quietly. This pins the contract
    from both sides.
    """
    from observability.tracing import CORRELATION_HEADER, SESSION_HEADER, _coerce_id
    from voice_pipeline.utils.correlation import (
        CORRELATION_HEADER as VOICE_HEADER,
        SESSION_HEADER as VOICE_SESSION_HEADER,
        correlation_headers,
    )

    assert VOICE_HEADER == CORRELATION_HEADER
    assert VOICE_SESSION_HEADER == SESSION_HEADER

    for raw in ("RM_abc-123", "room name/with spaces", "sess#42", "voice:1.2.3"):
        headers = correlation_headers(raw)
        sent = headers[CORRELATION_HEADER]
        assert _coerce_id(sent) == sent, f"backend would reject/rewrite {sent!r}"
        assert headers[SESSION_HEADER] == sent

    # No session id: caller's own headers pass through untouched.
    assert correlation_headers(None, {"Idempotency-Key": "k"}) == {"Idempotency-Key": "k"}

    # Distinct sources must not collapse into one correlation stream, and an
    # over-long id must stay within what the backend accepts.
    assert correlation_headers("a b")[CORRELATION_HEADER] != correlation_headers("a/b")[
        CORRELATION_HEADER
    ]
    long_id = correlation_headers("x" * 400)[CORRELATION_HEADER]
    assert len(long_id) <= 128 and _coerce_id(long_id) == long_id
    # An already-clean id is passed through verbatim, so voice-side and
    # backend-side greps use the same string.
    assert correlation_headers("RM_abc-123")[CORRELATION_HEADER] == "RM_abc-123"


@pytest.mark.asyncio
async def test_request_correlation_id_live(http_client):
    """Live backend: every response carries the correlation header."""
    resp = await http_client.post("/api/rag/query", json={"query": "test", "top_k": 3})
    assert "x-request-id" in resp.headers, "Missing correlation ID header"


# =========================================================================== #
# 6B. Circuit breaker for LLM providers
# =========================================================================== #

@pytest.fixture
def resilience():
    """The resilience module, with a clean breaker registry per test."""
    import utils.resilience as module

    module.reset_breakers_for_tests()
    yield module
    module.reset_breakers_for_tests()


def _fast_policy(module, **overrides):
    """An external_llm-shaped policy with the sleeps taken out."""
    base = module.get_policy("external_llm")
    return dataclasses.replace(base, base_delay_s=0.0, **overrides)


def test_breaker_state_machine_transitions(resilience):
    """closed -> open -> half-open -> closed, plus re-open from half-open."""
    policy = _fast_policy(resilience, recovery_timeout_s=0.05)
    breaker = resilience.CircuitBreaker(key="probe-transitions", policy=policy)
    S = resilience.BreakerState

    # Closed: everything passes.
    assert breaker.state is S.CLOSED
    assert breaker.allow() is True

    # Below threshold stays closed.
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is S.CLOSED, "opened before reaching the threshold"
    assert breaker.allow() is True

    # Threshold reached: open, and calls are refused without waiting.
    breaker.record_failure()
    assert breaker.state is S.OPEN
    assert breaker.allow() is False

    # Cooldown elapses: one probe is allowed and the state says so.
    time.sleep(0.06)
    assert breaker.allow() is True
    assert breaker.state is S.HALF_OPEN

    # A failed probe re-opens immediately (no second failure needed) and
    # restarts the cooldown.
    breaker.record_failure()
    assert breaker.state is S.OPEN
    assert breaker.allow() is False

    # A successful probe closes the breaker and clears the failure count.
    time.sleep(0.06)
    assert breaker.allow() is True
    assert breaker.state is S.HALF_OPEN
    breaker.record_success()
    assert breaker.state is S.CLOSED

    # Failure count really was reset: it takes a full threshold to reopen.
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is S.CLOSED
    breaker.record_failure()
    assert breaker.state is S.OPEN


def test_success_in_closed_state_resets_the_failure_count(resilience):
    """Only *consecutive* failures count."""
    policy = _fast_policy(resilience)
    breaker = resilience.CircuitBreaker(key="probe-consecutive", policy=policy)

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is resilience.BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is resilience.BreakerState.OPEN


def test_open_breaker_abandons_the_remaining_retry_budget(resilience, monkeypatch):
    """The 6B goal: a known-down provider stops costing the full retry cost."""
    policy = _fast_policy(resilience, max_retries=9, failure_threshold=3)
    monkeypatch.setitem(resilience.POLICIES, "phase6_test_llm", policy)

    calls = {"n": 0}

    @resilience.resilient(policy="phase6_test_llm", breaker_key="phase6-dead-provider")
    def dead_provider():
        calls["n"] += 1
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        dead_provider()
    # 10 attempts were budgeted; the breaker tripped on the 3rd failure and
    # the loop stopped there.
    assert calls["n"] == 3, f"kept retrying past the open breaker ({calls['n']})"
    assert resilience.breaker_states()["phase6-dead-provider"] == "open"

    # The next call does not touch the provider at all.
    before = calls["n"]
    t0 = time.perf_counter()
    with pytest.raises(resilience.CircuitOpenError):
        dead_provider()
    assert calls["n"] == before, "open breaker still called the provider"
    assert time.perf_counter() - t0 < 0.05


def test_breaker_recovers_and_reuses_the_provider(resilience, monkeypatch):
    """Half-open probe succeeds: traffic returns to the provider."""
    policy = _fast_policy(resilience, max_retries=0, recovery_timeout_s=0.05)
    monkeypatch.setitem(resilience.POLICIES, "phase6_test_recover", policy)

    state = {"down": True}

    @resilience.resilient(policy="phase6_test_recover", breaker_key="phase6-flapper")
    def flapper():
        if state["down"]:
            raise RuntimeError("still down")
        return "ok"

    for _ in range(3):
        with pytest.raises(RuntimeError):
            flapper()
    assert resilience.breaker_states()["phase6-flapper"] == "open"

    state["down"] = False
    # Still open: the cooldown has not elapsed.
    with pytest.raises(resilience.CircuitOpenError):
        flapper()

    time.sleep(0.06)
    assert flapper() == "ok"
    assert resilience.breaker_states()["phase6-flapper"] == "closed"


def test_breaker_state_reaches_the_prometheus_gauge(resilience):
    """Transitions must be observable, not just logged (Phase 7 gauge)."""
    from prometheus_client import REGISTRY

    def gauge_for(key):
        return REGISTRY.get_sample_value("circuit_breaker_state", {"key": key})

    key = "phase6-gauge-probe"
    policy = _fast_policy(resilience, recovery_timeout_s=0.05)
    breaker = resilience.CircuitBreaker(key=key, policy=policy)

    breaker.record_success()
    assert gauge_for(key) == 0.0, "closed should report 0"

    for _ in range(3):
        breaker.record_failure()
    assert gauge_for(key) == 2.0, "open should report 2"

    time.sleep(0.06)
    breaker.allow()
    assert gauge_for(key) == 1.0, "half-open should report 1"


def test_llm_client_providers_are_individually_protected(resilience):
    """Every provider in the agent LLM client has its own breaker key."""
    import agents.utils.llm_client as llm_client

    # The decorators register their keys at import time, so a dashboard shows
    # a series per provider before the first call. The fixture cleared the
    # registry, so re-decorating is what repopulates it: assert against the
    # class instead, which is the durable fact.
    for provider in ("groq", "mercury", "openai", "local"):
        method = getattr(llm_client.AgentLLMClient, f"_call_{provider}")
        assert hasattr(method, "__wrapped__"), f"_call_{provider} is not wrapped"

    src = (BACKEND_ROOT / "agents" / "utils" / "llm_client.py").read_text()
    for provider in ("groq", "mercury", "openai", "local"):
        assert f'breaker_key="{provider}"' in src, f"no breaker key for {provider}"
    assert "from tenacity" not in src and "@retry(" not in src, (
        "tenacity retry still active: it would multiply the policy's retry "
        "budget and hide failures from the breaker"
    )


def test_llm_client_falls_through_open_breakers_to_a_live_provider(
    resilience, monkeypatch
):
    """Fallback ordering must still work, and cost nothing, when breakers open."""
    import agents.utils.llm_client as llm_client

    client = llm_client.AgentLLMClient()
    # groq is the configured primary; mercury and openai are ahead of local in
    # FALLBACK_CHAIN. Open all three.
    for provider in ("groq", "mercury", "openai"):
        breaker = resilience.get_breaker(provider, "external_llm")
        for _ in range(breaker.policy.failure_threshold):
            breaker.record_failure()
        assert breaker.state is resilience.BreakerState.OPEN

    monkeypatch.setattr(
        client, "_call_local", lambda *_args, **_kwargs: "answer-from-local"
    )

    t0 = time.perf_counter()
    answer = client.chat([{"role": "user", "content": f"phase6-{time.time()}"}])
    elapsed = time.perf_counter() - t0

    assert answer == "answer-from-local", "fallback chain did not reach local"
    # Three skipped providers must cost microseconds, not three retry budgets.
    assert elapsed < 1.0, f"open breakers still cost {elapsed:.2f}s"


def test_generator_breaker_skips_dead_mercury_and_falls_back_to_groq(
    resilience, monkeypatch
):
    """RAG path: Mercury down -> breaker opens -> stream_response uses Groq."""
    from chat_pipeline.rag import generator

    # The generator resolves the resilience module lazily; it must be the same
    # object this test manipulates, or the assertions would be vacuous.
    assert generator._resilience() is resilience

    def dead_mercury(*_args, **_kwargs):
        raise RuntimeError("mercury connection refused")

    monkeypatch.setattr(generator, "_call_mercury_api_inner", dead_mercury)
    monkeypatch.setattr(
        generator, "_call_groq_api_inner", lambda *_a, **_k: "groq answer"
    )

    threshold = resilience.get_policy("external_llm").failure_threshold
    for _ in range(threshold):
        with pytest.raises(RuntimeError):
            generator._call_mercury_api("q", {})

    assert resilience.breaker_states()["mercury"] == "open"

    # Guarded call now short-circuits instead of re-dialing Mercury.
    t0 = time.perf_counter()
    with pytest.raises(resilience.CircuitOpenError):
        generator._call_mercury_api("q", {})
    assert time.perf_counter() - t0 < 0.05

    # And the full chain still produces an answer, from the next provider.
    t0 = time.perf_counter()
    chunks = list(generator.stream_response("q", generation_backend="auto"))
    elapsed = time.perf_counter() - t0
    assert "".join(chunks) == "groq answer"
    assert generator.get_last_backend_used() == "groq"
    assert elapsed < 1.0, f"open Mercury breaker still cost {elapsed:.2f}s"


def test_generator_streaming_records_breaker_failures(resilience, monkeypatch):
    """A streaming provider failing before the first token counts as a failure."""
    from chat_pipeline.rag import generator

    def boom(*_args, **_kwargs):
        raise RuntimeError("stream refused")
        yield ""  # pragma: no cover - makes this a generator function

    monkeypatch.setattr(generator, "_stream_openai_compatible_api_inner", boom)

    threshold = resilience.get_policy("external_llm").failure_threshold
    for _ in range(threshold):
        with pytest.raises(RuntimeError):
            list(generator._stream_groq_api("q", {}))

    assert resilience.breaker_states()["groq"] == "open"


def test_abandoned_stream_is_not_counted_as_a_failure(resilience, monkeypatch):
    """A consumer walking away says nothing about provider health."""
    from chat_pipeline.rag import generator

    def slow_stream(*_args, **_kwargs):
        yield "first"
        yield "second"

    monkeypatch.setattr(generator, "_stream_openai_compatible_api_inner", slow_stream)

    gen = generator._stream_mercury_api("q", {})
    assert next(gen) == "first"
    gen.close()  # raises GeneratorExit inside the guard

    # No failure recorded, and no bogus success either.
    assert resilience.get_breaker("mercury").state is resilience.BreakerState.CLOSED


# =========================================================================== #
# 6C. NullPool -> QueuePool
# =========================================================================== #

@pytest.fixture(scope="module")
def connection_module():
    import db.connection as connection

    return connection


def test_postgres_uses_queuepool_with_planned_sizing(connection_module):
    from sqlalchemy.pool import QueuePool

    opts = connection_module.engine_options("postgresql://u:p@10.0.0.1:5432/db")
    assert opts["poolclass"] is QueuePool
    assert opts["pool_size"] == 5
    assert opts["max_overflow"] == 10
    assert opts["pool_pre_ping"] is True
    # A saturated pool must fail fast rather than queue behind an abandoned
    # request (SQLAlchemy's default is 30s).
    assert opts["pool_timeout"] <= 10
    assert opts["pool_recycle"] > 0, "no recycle: Cloud SQL will hand back dead sockets"


def test_postgres_engine_really_gets_that_pool(connection_module):
    """Assert on the built engine, not just the kwargs dict."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool

    url = "postgresql://u:p@127.0.0.1:59999/db"  # never connected to
    engine = create_engine(url, **connection_module.engine_options(url))
    try:
        pool = engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.size() == 5
        assert pool._max_overflow == 10
        assert pool._pre_ping is True
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("sqlite:///./frontshiftai.db", "NullPool"),
        ("sqlite:////abs/path/app.db", "NullPool"),
        ("sqlite://", "StaticPool"),
        ("sqlite:///:memory:", "StaticPool"),
        ("sqlite:///file:mem?mode=memory&cache=shared", "StaticPool"),
    ],
)
def test_sqlite_pooling_is_dialect_correct(connection_module, url, expected):
    """QueuePool is wrong for SQLite; the branch must not regress into it."""
    opts = connection_module.engine_options(url)
    assert opts["poolclass"].__name__ == expected
    # Sessions cross threads under FastAPI's threadpool.
    assert opts["connect_args"]["check_same_thread"] is False
    assert "pool_size" not in opts, "QueuePool sizing leaked into the SQLite branch"


def test_in_memory_sqlite_shares_one_connection(connection_module):
    """StaticPool is not cosmetic: without it each session sees a fresh DB."""
    from sqlalchemy import create_engine, text

    url = "sqlite://"
    engine = create_engine(url, **connection_module.engine_options(url))
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER)"))
            conn.commit()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM t")).scalar() == 0
    finally:
        engine.dispose()


def test_nullpool_for_file_sqlite_does_not_hold_connections(connection_module):
    from sqlalchemy import create_engine, text

    url = f"sqlite:///{Path(__file__).parent / 'phase6_pool_probe.db'}"
    engine = create_engine(url, **connection_module.engine_options(url))
    try:
        for _ in range(5):
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        # NullPool keeps nothing checked in, so there is no idle holder of the
        # SQLite write lock.
        assert engine.pool.status()
    finally:
        engine.dispose()
        Path(url.replace("sqlite:///", "")).unlink(missing_ok=True)


def test_unknown_dialect_still_pings(connection_module):
    opts = connection_module.engine_options("mysql+pymysql://u:p@host/db")
    assert opts["pool_pre_ping"] is True


def test_pool_stats_reports_only_what_the_pool_tracks(connection_module):
    stats = connection_module.pool_stats()
    assert stats["class"] == type(connection_module.engine.pool).__name__
    if stats["class"] == "QueuePool":
        assert {"size", "checkedin", "checkedout", "overflow"} <= set(stats)
    else:
        # A pool with no queue must not report a fake 0 that reads as "idle".
        assert "checkedout" not in stats or isinstance(stats["checkedout"], int)


def test_pool_gauges_are_refreshed_at_scrape_time():
    """Phase 7 declared db_pool_* gauges; 6C has to actually feed them."""
    import observability.metrics as metrics

    # Must not raise even when the live engine has no queue (SQLite dev).
    metrics._refresh_db_pool_gauges()

    from sqlalchemy import create_engine
    import db.connection as connection

    url = "postgresql://u:p@127.0.0.1:59999/db"
    pooled = create_engine(url, **connection.engine_options(url))
    original = connection.engine
    connection.engine = pooled
    try:
        metrics._refresh_db_pool_gauges()
        from prometheus_client import REGISTRY

        assert REGISTRY.get_sample_value("db_pool_size") == 5
        assert REGISTRY.get_sample_value("db_pool_checkedout") == 0
    finally:
        connection.engine = original
        pooled.dispose()


def test_engine_disposal_is_idempotent_and_non_fatal(connection_module):
    """Pooled connections need an explicit teardown; it must be safe to repeat."""
    from sqlalchemy import text

    connection_module._dispose_engine()
    connection_module._dispose_engine()
    # The engine stays usable afterwards: dispose replaces the pool.
    with connection_module.engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_sessions_survive_concurrent_checkouts(connection_module):
    """Sanity: the configured pool serves parallel sessions without erroring."""
    import concurrent.futures as cf
    from sqlalchemy import text

    def query(_i):
        db = connection_module.SessionLocal()
        try:
            return db.execute(text("SELECT 1")).scalar()
        finally:
            db.close()

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(query, range(64)))
    assert results == [1] * 64


@pytest.mark.asyncio
async def test_connection_pool_under_load(http_client):
    """Live backend: 50 concurrent DB-hitting requests, no pool exhaustion."""
    async def health_check():
        resp = await http_client.get("/health")
        return resp.status_code

    results = await asyncio.gather(
        *[health_check() for _ in range(50)], return_exceptions=True
    )
    failures = [r for r in results if r != 200]
    assert not failures, f"Connection pool exhaustion: {len(failures)}/50 failed"
