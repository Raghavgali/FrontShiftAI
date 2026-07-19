"""
Phase 3 stress tests: Infrastructure Resilience.

Covers:
- 3B: on-demand Modal voice worker is supervised (bounded restarts +
  heartbeat watchdog), and the worker emits heartbeats for the watchdog
- 3C: deploy workflow gates on /health/ready and rolls traffic back to the
  previous Cloud Run revision when the new one never becomes healthy
- 3D: backend drains in-flight requests on shutdown instead of dropping them

Note on 3A (Modal keep_warm): intentionally NOT implemented. Keeping a
worker warm costs ~$1.50/day; the accepted posture is scale-to-zero where
the first request of the day may hit a cold start and every request after
that is served warm. The latency test below asserts only the warm path.

Run:
    STRESS_TEST_JWT=<token> pytest stress_tests/test_phase3_infra.py -v -s
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from conftest import LatencyReport

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-backend.yml"
MODAL_DEPLOY = REPO_ROOT / "voice_pipeline" / "modal_deploy.py"
SUPERVISOR = REPO_ROOT / "voice_pipeline" / "utils" / "process_supervisor.py"
VOICE_MAIN = REPO_ROOT / "voice_pipeline" / "scripts" / "main.py"
BACKEND_MAIN = REPO_ROOT / "backend" / "main.py"


# --------------------------------------------------------------------------- #
# 3C. Readiness endpoint the deploy gate polls
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backend_health_ready(http_client):
    """/health/ready returns 200 with every model actually loaded."""
    resp = await http_client.get("/health/ready")
    assert resp.status_code == 200, f"/health/ready returned {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "ready"
    assert data.get("database") == "connected"
    models = data.get("models", {})
    assert models.get("embedding") == "loaded", f"embedding: {models.get('embedding')}"
    assert str(models.get("chromadb", "")).startswith("loaded"), (
        f"chromadb: {models.get('chromadb')}"
    )


@pytest.mark.asyncio
async def test_warm_requests_are_fast(http_client):
    """After the first (possibly cold) request, the warm path stays fast."""
    ITERATIONS = 8

    # First request absorbs any cold start; it is deliberately not timed.
    await http_client.get("/health/ready")

    report = LatencyReport("Warm /health/ready", target_p50=1.0, target_p95=2.0)
    for _ in range(ITERATIONS):
        start = time.time()
        resp = await http_client.get("/health/ready")
        report.record(time.time() - start)
        assert resp.status_code == 200
    report.assert_targets()


# --------------------------------------------------------------------------- #
# 3C. Deploy workflow health gate + rollback (static verification)
# --------------------------------------------------------------------------- #
def test_deploy_workflow_has_health_gate():
    """Deploy workflow must verify /health/ready after gcloud run deploy."""
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    steps = [
        step
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
    ]
    verify = [
        s for s in steps
        if "health" in s.get("name", "").lower() or "verify" in s.get("name", "").lower()
    ]
    assert verify, "Deploy workflow missing a health verification step"
    run = verify[0].get("run", "")
    assert "/health/ready" in run, "Health gate does not poll /health/ready"
    assert "gcloud run services describe" in run, (
        "Health gate must resolve the service URL instead of assuming an env var"
    )


def test_deploy_workflow_rolls_back_to_previous_revision():
    """On a failed health gate, traffic must return to the prior revision."""
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    runs = " ".join(
        step.get("run", "")
        for job in workflow.get("jobs", {}).values()
        for step in job.get("steps", [])
    )
    assert "update-traffic" in runs, "No traffic rollback in deploy workflow"
    assert "PREV_REVISION" in runs, (
        "Rollback must target the previous revision explicitly, not LATEST=0"
    )


# --------------------------------------------------------------------------- #
# 3B. Worker supervision wiring (static verification)
# --------------------------------------------------------------------------- #
def test_modal_worker_is_supervised():
    """voice_worker_for_room must run the agent under the supervisor."""
    src = MODAL_DEPLOY.read_text()
    assert "run_supervised_process" in src, "Worker not wrapped in supervisor"
    assert "max_restarts" in src and "heartbeat_timeout" in src

    supervisor = SUPERVISOR.read_text()
    assert "def run_supervised_process" in supervisor
    assert "heartbeat" in supervisor.lower()


def test_voice_worker_emits_heartbeat():
    """The agent process must produce output the watchdog can observe."""
    src = VOICE_MAIN.read_text()
    assert "WORKER_HEARTBEAT" in src, "Worker heartbeat emitter missing"
    assert "_emit_worker_heartbeat" in src


# --------------------------------------------------------------------------- #
# 3D. Graceful shutdown drain (static verification)
# --------------------------------------------------------------------------- #
def test_backend_drains_inflight_requests_on_shutdown():
    """Shutdown must wait for in-flight requests, bounded, not a blind sleep."""
    src = BACKEND_MAIN.read_text()
    assert "_inflight_requests" in src, "No in-flight request counter"
    assert "draining in-flight requests" in src, "No drain log in shutdown path"
    assert "await asyncio.sleep(10)" not in src, (
        "Shutdown still uses a fixed 10s sleep instead of a real drain"
    )
