"""Phase 7H — Locust load generator with Prometheus export.

Runs a mixed workload (RAG queries + unified chat + health pings) against
the backend and exposes Locust's own stats on a Prometheus scrape endpoint.
Grafana Agent picks it up alongside the backend's ``/metrics`` so the
stress_test dashboard overlays load and backend p95 in one view — that's
the whole point: you can *see* the knee where latency starts climbing.

Run::

    export STRESS_TEST_JWT=<access_token>
    export STRESS_TEST_BACKEND_URL=http://localhost:8000
    locust -f stress_tests/locustfile.py --host $STRESS_TEST_BACKEND_URL

Prometheus endpoint defaults to ``:9646/metrics`` (standard Locust
exporter port) so the Grafana Agent scrape config in
``grafana/README.md`` works out of the box.
"""
from __future__ import annotations

import os
import random
import threading
from typing import Optional

from locust import HttpUser, between, events, task
from prometheus_client import Counter, Gauge, Histogram, start_http_server


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------

QUERIES = [
    "What is the PTO policy?",
    "How many sick days do I get?",
    "What are the company holidays?",
    "What is the dress code?",
    "How does the 401k match work?",
    "What is the remote work policy?",
    "How do I submit an expense report?",
    "What is parental leave?",
    "How many vacation days do I have left?",
    "What is bereavement leave?",
]


def _auth_headers() -> dict:
    token = os.getenv("STRESS_TEST_JWT")
    if not token:
        raise RuntimeError("Set STRESS_TEST_JWT before running locustfile.py")
    return {"Authorization": f"Bearer {token}"}


class FrontShiftAIUser(HttpUser):
    """Simulates a mix of RAG / chat / health traffic at human-ish pacing."""

    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers.update(_auth_headers())

    @task(5)
    def rag_query_voice_profile(self):
        """Voice-shaped RAG — short max_tokens, Groq backend."""
        self.client.post(
            "/api/rag/query",
            name="RAG (voice profile)",
            json={
                "query": random.choice(QUERIES),
                "top_k": 3,
                "max_tokens": 256,
                "generation_backend": "groq",
            },
        )

    @task(3)
    def rag_query_chat_profile(self):
        """Chat-shaped RAG — defaults (Mercury primary, longer answers)."""
        self.client.post(
            "/api/rag/query",
            name="RAG (chat profile)",
            json={"query": random.choice(QUERIES), "top_k": 3},
        )

    @task(2)
    def unified_chat(self):
        self.client.post(
            "/api/chat/message",
            name="Unified chat",
            json={"message": random.choice(QUERIES)},
        )

    @task(1)
    def health(self):
        # Health check is unauthenticated but we keep the header for
        # consistency — backend ignores it.
        self.client.get("/health", name="Health")


# ---------------------------------------------------------------------------
# Prometheus exporter
# ---------------------------------------------------------------------------
#
# Locust doesn't ship a Prometheus exporter in its core distribution
# anymore, so we roll a tiny one here: hook the request event, observe
# latency/status, and expose a small gauge set for users/RPS. This stays
# in-process (no extra deps) and matches the metrics the stress_test
# Grafana dashboard queries.

locust_requests_total = Counter(
    "locust_requests_total",
    "Locust-issued requests.",
    labelnames=("endpoint", "method", "status"),
)

locust_request_latency_seconds = Histogram(
    "locust_request_latency_seconds",
    "Locust-observed request latency.",
    labelnames=("endpoint", "method"),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

locust_users = Gauge(
    "locust_users",
    "Active simulated users.",
)


@events.request.add_listener
def _on_request(request_type, name, response_time, response_length,
                exception, context, **kwargs):
    # response_time is in milliseconds; Prometheus histograms prefer seconds.
    status = "error" if exception else "ok"
    locust_requests_total.labels(name, request_type, status).inc()
    if response_time is not None:
        locust_request_latency_seconds.labels(name, request_type).observe(response_time / 1000.0)


@events.spawning_complete.add_listener
def _on_spawn_complete(user_count, **_kwargs):
    locust_users.set(user_count)


@events.test_stop.add_listener
def _on_test_stop(**_kwargs):
    locust_users.set(0)


_exporter_lock = threading.Lock()
_exporter_started = False


@events.init.add_listener
def _start_exporter(environment, **_kwargs):
    """Bring up the scrape endpoint once per Locust process."""
    global _exporter_started
    with _exporter_lock:
        if _exporter_started:
            return
        port = int(os.getenv("LOCUST_PROM_PORT", "9646"))
        try:
            start_http_server(port)
            _exporter_started = True
            print(f"[locust-prometheus] exporter listening on :{port}/metrics")
        except OSError as exc:
            print(f"[locust-prometheus] could not bind :{port}: {exc}")
