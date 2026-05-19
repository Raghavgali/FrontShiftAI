"""Phase 7 — Prometheus/Grafana plumbing sanity.

Exercises the scrape endpoint + label-cardinality discipline without
needing Grafana Cloud to actually be configured. A CI-friendly test:
if someone adds a ``user_id`` label in a future PR, this fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# ---- /metrics exposition ----------------------------------------------------

@pytest.mark.asyncio
async def test_metrics_endpoint_is_prometheus_format(http_client):
    resp = await http_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "# HELP" in body, "missing Prometheus HELP lines"
    assert "# TYPE" in body, "missing Prometheus TYPE lines"
    # Spot-check: every instrument we declare shows up at least once.
    for expected in (
        "http_requests_total",
        "http_request_duration_seconds",
        "rag_retrieval_duration_seconds",
        "llm_provider_latency_seconds",
        "circuit_breaker_state",
        "tenant_request_total",
    ):
        assert expected in body, f"missing metric {expected}"


# ---- Label cardinality discipline ------------------------------------------

_FORBIDDEN_LABELS = ("user_id", "request_id", "conversation_id", "ticket_id", "email")


def test_no_high_cardinality_labels_in_metrics_module():
    """Static check — forbidden labels must not appear in metric definitions."""
    path = Path(__file__).resolve().parents[1] / "backend" / "observability" / "metrics.py"
    src = path.read_text()
    # Only flag occurrences inside a labelnames=(...) tuple to avoid false
    # positives from comments/strings elsewhere.
    labelname_blocks = re.findall(r"labelnames\s*=\s*\(([^)]*)\)", src)
    for block in labelname_blocks:
        for forbidden in _FORBIDDEN_LABELS:
            assert forbidden not in block, (
                f"forbidden high-cardinality label {forbidden!r} in metrics module"
            )


# ---- X-Request-ID propagation ----------------------------------------------

@pytest.mark.asyncio
async def test_request_id_round_trips(http_client):
    """Client-supplied X-Request-ID is echoed back on the response."""
    rid = "test-correlation-abc123"
    resp = await http_client.get("/health", headers={"X-Request-ID": rid})
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID") == rid


@pytest.mark.asyncio
async def test_request_id_generated_when_absent(http_client):
    """Server generates a fresh X-Request-ID if the client didn't send one."""
    resp = await http_client.get("/health")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid, "server should generate an X-Request-ID"
    # UUID4 hex length.
    assert re.fullmatch(r"[0-9a-f]{32}", rid), f"unexpected rid format: {rid!r}"


# ---- Grafana artifacts are well-formed -------------------------------------

def test_grafana_dashboards_parse():
    grafana_dir = Path(__file__).resolve().parents[1] / "grafana" / "dashboards"
    dashboards = sorted(grafana_dir.glob("*.json"))
    assert len(dashboards) == 6, f"expected 6 dashboards, got {[d.name for d in dashboards]}"
    for d in dashboards:
        obj = json.loads(d.read_text())
        assert obj.get("uid"), f"{d.name}: missing uid"
        assert obj.get("title"), f"{d.name}: missing title"
        assert isinstance(obj.get("panels"), list) and obj["panels"], (
            f"{d.name}: missing/empty panels"
        )


def test_slo_yaml_declares_required_slos():
    """Every promised SLO exists; alerts.yaml references at least one of them."""
    import os
    slo_path = Path(__file__).resolve().parents[1] / "grafana" / "slo.yaml"
    alerts_path = Path(__file__).resolve().parents[1] / "grafana" / "alerts.yaml"
    text = slo_path.read_text()
    for name in (
        "voice_p95_latency",
        "chat_p95_latency",
        "availability_per_tenant",
        "rag_retrieval_p95",
        "llm_generation_p95_groq",
        "voice_session_creation_p95",
    ):
        assert name in text, f"SLO missing: {name}"

    # Alerts file must exist and mention at least one SLO.
    assert alerts_path.exists()
    assert "voice_p95_latency" in alerts_path.read_text()
