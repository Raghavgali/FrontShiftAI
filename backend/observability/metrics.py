"""Prometheus instruments for the FrontShiftAI backend.

Single source of truth for every counter, histogram, and gauge. Keep this
file tight — adding a metric means adding a label-cardinality concern, so
every new one gets a reason in a comment here before it lands.

Label discipline (enforced by ``test_label_cardinality_bounded`` in Phase 7
tests):
    - ``company``   bounded (~200 tenants max)
    - ``endpoint``  bounded (~20 paths; see ``_normalize_endpoint``)
    - ``status``    bounded (2xx/3xx/4xx/5xx classes)
    - ``method``    bounded (GET/POST/PUT/DELETE/PATCH)
    - ``provider``  bounded (mercury/groq/openai/local)
    - ``agent``     bounded (rag/pto/hr_ticket/website_extraction)

    Never: ``user_id``, ``request_id``, ``conversation_id``, ``ticket_id`` —
    those are unbounded. They belong in logs (correlated via X-Request-ID),
    not metrics.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

from fastapi import Request, Response
from fastapi.responses import Response as FastAPIResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP golden signals
# ---------------------------------------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests served by the backend.",
    labelnames=("method", "endpoint", "status", "company"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "End-to-end request latency.",
    labelnames=("method", "endpoint", "company"),
    # Buckets picked to resolve the interesting p50/p95 band we actually care
    # about: sub-second for most endpoints, a long tail for RAG generation.
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

# ---------------------------------------------------------------------------
# RAG-specific
# ---------------------------------------------------------------------------

rag_retrieval_duration_seconds = Histogram(
    "rag_retrieval_duration_seconds",
    "Vector-store retrieval latency.",
    labelnames=("company",),
    buckets=(0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
)

rag_generation_duration_seconds = Histogram(
    "rag_generation_duration_seconds",
    "LLM generation latency (retrieved-context → answer).",
    labelnames=("backend", "company"),
    buckets=(0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0),
)

rag_cache_hit_total = Counter(
    "rag_cache_hit_total",
    "RAG pipeline in-memory LRU cache outcomes.",
    labelnames=("company", "hit"),   # hit in {"1", "0"}
)

# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------

llm_provider_latency_seconds = Histogram(
    "llm_provider_latency_seconds",
    "External LLM API latency, by outcome.",
    labelnames=("provider", "outcome"),  # outcome in {"success","error","timeout","429"}
    buckets=(0.1, 0.3, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

llm_provider_failures_total = Counter(
    "llm_provider_failures_total",
    "External LLM failures by class.",
    labelnames=("provider", "error_class"),  # {"timeout","429","5xx","connection","other"}
)

circuit_breaker_state = Gauge(
    "circuit_breaker_state",
    "Resilience circuit-breaker state (0=closed, 1=half_open, 2=open).",
    labelnames=("key",),
)

# ---------------------------------------------------------------------------
# DB pool (populated lazily when QueuePool lands in Phase 6C)
# ---------------------------------------------------------------------------

db_pool_size = Gauge(
    "db_pool_size",
    "Current DB connection pool size (configured).",
)

db_pool_checkedout = Gauge(
    "db_pool_checkedout",
    "Currently checked-out DB connections.",
)

# ---------------------------------------------------------------------------
# Tenant / agent
# ---------------------------------------------------------------------------

tenant_request_total = Counter(
    "tenant_request_total",
    "Requests routed to each agent, by tenant.",
    labelnames=("company", "agent"),
)


# ---------------------------------------------------------------------------
# Middleware + /metrics
# ---------------------------------------------------------------------------

# Explicit allow-list for endpoint labels. Unlabeled paths (including any
# path-param variants like /api/chat/conversations/<uuid>/messages) roll up
# to a single "other" bucket so label cardinality stays bounded.
_KNOWN_ENDPOINTS = {
    "/",
    "/health",
    "/health/ready",
    "/metrics",
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/api/auth/voice-token",
    "/api/auth/me",
    "/api/chat/message",
    "/api/chat/conversations",
    "/api/chat/health",
    "/api/rag/query",
    "/api/pto/chat",
    "/api/pto/balance",
    "/api/pto/requests",
    "/api/hr-tickets/chat",
    "/api/hr-tickets/my-tickets",
    "/api/admin/company-admins",
    "/api/admin/company-users",
    "/api/admin/all-companies",
    "/api/admin/companies",
    "/api/admin/monitoring/stats",
}


def _normalize_endpoint(path: str) -> str:
    if path in _KNOWN_ENDPOINTS:
        return path
    # Collapse conversation/ticket/etc. subresources and anything admin-ish.
    for prefix in (
        "/api/chat/conversations/",
        "/api/pto/admin/",
        "/api/hr-tickets/",
        "/api/hr-tickets/admin/",
        "/api/admin/",
    ):
        if path.startswith(prefix):
            return prefix + "*"
    return "other"


def _status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


async def prometheus_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Observe every HTTP request.

    Pulls ``company`` from the tenant ContextVar (set by the Phase 0.6
    middleware) so the label is correct even when the handler itself
    doesn't touch it.
    """
    start = time.perf_counter()
    endpoint = _normalize_endpoint(request.url.path)
    method = request.method

    # /metrics is self-reported; labeling its own scrape would add noise.
    if request.url.path == "/metrics":
        return await call_next(request)

    try:
        response = await call_next(request)
        status = _status_class(response.status_code)
    except Exception:
        # Exception bubbles up to FastAPI's handler which returns 500.
        # Record it here before re-raising so we don't miss the metric.
        duration = time.perf_counter() - start
        company = _current_company_label()
        http_requests_total.labels(method, endpoint, "5xx", company).inc()
        http_request_duration_seconds.labels(method, endpoint, company).observe(duration)
        raise

    duration = time.perf_counter() - start
    company = _current_company_label()
    http_requests_total.labels(method, endpoint, status, company).inc()
    http_request_duration_seconds.labels(method, endpoint, company).observe(duration)
    return response


def _current_company_label() -> str:
    """Read the tenant ContextVar, coerced to a bounded label value."""
    try:
        from db.tenant_context import get_current_company  # local import avoids cycle
        value = get_current_company()
    except Exception:
        value = None
    if value is None:
        return "none"
    value = str(value)[:64]  # defensive truncation
    return value


def metrics_endpoint() -> FastAPIResponse:
    """Serve the Prometheus scrape payload."""
    return FastAPIResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ---------------------------------------------------------------------------
# Helpers used by other modules (RAG pipeline, LLM generator, ...)
# ---------------------------------------------------------------------------

def observe_rag_retrieval(company: Optional[str], seconds: float) -> None:
    rag_retrieval_duration_seconds.labels(company or "none").observe(seconds)


def observe_rag_generation(
    backend: Optional[str], company: Optional[str], seconds: float
) -> None:
    rag_generation_duration_seconds.labels(backend or "unknown", company or "none").observe(seconds)


def observe_rag_cache(company: Optional[str], hit: bool) -> None:
    rag_cache_hit_total.labels(company or "none", "1" if hit else "0").inc()


def observe_llm_call(
    provider: str, outcome: str, seconds: float, error_class: Optional[str] = None
) -> None:
    """One-liner used by the LLM generator around each provider call."""
    llm_provider_latency_seconds.labels(provider, outcome).observe(seconds)
    if outcome != "success" and error_class:
        llm_provider_failures_total.labels(provider, error_class).inc()


def observe_tenant_request(company: Optional[str], agent: str) -> None:
    tenant_request_total.labels(company or "none", agent).inc()


_BREAKER_STATE_VALUES = {"closed": 0, "half_open": 1, "open": 2}


def set_circuit_breaker_state(key: str, state: str) -> None:
    """Called from ``backend/utils/resilience.py`` on every state transition."""
    circuit_breaker_state.labels(key).set(_BREAKER_STATE_VALUES.get(state, 0))
