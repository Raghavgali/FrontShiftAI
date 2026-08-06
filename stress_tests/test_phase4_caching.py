"""
Phase 4 stress tests: Caching + Data Pipeline Resilience.

Covers:
- 4A: ``_get_all_companies`` is cached per collection (and *not* via
  ``lru_cache``, which cannot work on an unhashable Chroma Collection)
- 4B: ``resolve_company_filter`` is cached, bounded, and thread safe
- 4C: GCS/Chroma archive sync retries with backoff and verifies the artifact
- 4D: data pipeline stages write checkpoint markers that ``--resume`` honours
- 4E: PDF downloads retry, verify Content-Length, rename atomically, and never
  leave a partial file that could satisfy the "already exists" skip
- 4F: ChromaDB inserts are batched and deduplicated, and a failed batch names
  the batches that succeeded

The first three tests need a running deployment and are skipped unless
STRESS_TEST_JWT is set. Everything below them runs offline and deterministically.

Run:
    pytest stress_tests/test_phase4_caching.py -v
    STRESS_TEST_JWT=<token> pytest stress_tests/test_phase4_caching.py -v -s
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from conftest import LatencyReport

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BACKEND_DIR = REPO_ROOT / "backend"
TASKS_SOURCE = BACKEND_DIR / "jobs" / "tasks.py"

from chat_pipeline.rag import data_loader  # noqa: E402
from data_pipeline.scripts import download_data  # noqa: E402
from data_pipeline.scripts import pipeline_runner  # noqa: E402
from data_pipeline.scripts import store_in_chromadb  # noqa: E402


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeCollection:
    """Mimics the bits of a Chroma Collection the data loader touches.

    Crucially it is *unhashable* in the same way the real Collection is (it
    defines ``__eq__`` without ``__hash__``), so a regression back to
    ``@lru_cache`` on a collection argument fails these tests loudly.
    """

    def __init__(self, companies=(), peek_companies=None, fail_get=False):
        self.name = "frontshift_handbooks"
        self._companies = list(companies)
        self._peek_companies = (
            list(peek_companies) if peek_companies is not None else []
        )
        self.fail_get = fail_get
        self.get_calls = 0
        self.peek_calls = 0

    def __eq__(self, other):  # pragma: no cover - parity with chromadb
        return self is other

    __hash__ = None  # exactly what chromadb ends up with

    def get(self, **kwargs):
        self.get_calls += 1
        if self.fail_get:
            raise RuntimeError("chroma unavailable")
        return {"metadatas": [{"company": c} for c in self._companies]}

    def peek(self, **kwargs):
        self.peek_calls += 1
        return {"metadatas": [{"company": c} for c in self._peek_companies]}


class RecordingCollection:
    """Records every ``add()`` call so batching can be asserted."""

    def __init__(self, fail_on_batch=None):
        self.batches = []
        self.fail_on_batch = fail_on_batch

    def add(self, documents, metadatas, ids):
        self.batches.append(len(documents))
        if self.fail_on_batch is not None and len(self.batches) == self.fail_on_batch:
            raise RuntimeError("simulated chroma write failure")


class FakeResponse:
    """Minimal stand-in for a streamed ``requests`` response."""

    def __init__(self, status_code=200, body=b"", headers=None, raise_mid_stream=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers = headers or {}
        self._body = body
        self._raise_mid_stream = raise_mid_stream

    def iter_content(self, chunk_size=1):
        import requests as _requests

        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]
            if self._raise_mid_stream:
                raise _requests.exceptions.ConnectionError("peer reset")


@pytest.fixture(autouse=True)
def _clean_company_caches():
    data_loader.clear_company_caches()
    yield
    data_loader.clear_company_caches()


def _make_targz(members: dict) -> bytes:
    """Build a valid .tar.gz in memory from ``{name: bytes}``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════════════ #
# Live deployment tests (skipped without STRESS_TEST_JWT)
# ═══════════════════════════════════════════════════════════════════════════ #
@pytest.mark.asyncio
async def test_company_filter_cached(http_client):
    """Repeated queries should benefit from the company metadata cache."""
    ITERATIONS = 30
    payload = {
        "query": "What is the vacation policy?",
        "top_k": 3,
        "max_tokens": 256,
        "generation_backend": "groq",
    }

    await http_client.post("/api/rag/query", json=payload)  # warm cache

    report = LatencyReport("Cached company filter", target_p50=0.8, target_p95=1.5)
    for _ in range(ITERATIONS):
        start = time.time()
        r = await http_client.post("/api/rag/query", json=payload)
        r.raise_for_status()
        report.record(time.time() - start)
    report.assert_targets()


@pytest.mark.asyncio
async def test_pipeline_cache_hit(http_client):
    """An identical query should come back as a cache hit, and fast.

    ``cache_hit`` is a real field on the response model (backend/schemas/rag.py)
    fed from ``timings["cache_hit"]`` by the RAG pipeline, so asserting on it is
    meaningful rather than vacuous.
    """
    payload = {"query": "What is the dress code?", "top_k": 3, "max_tokens": 256}
    await http_client.post("/api/rag/query", json=payload)

    start = time.time()
    r = await http_client.post("/api/rag/query", json=payload)
    hit_time = time.time() - start
    r.raise_for_status()
    assert r.json().get("cache_hit") is True, "second identical query was not a cache hit"
    assert hit_time < 0.2, f"cache hit took {hit_time:.3f}s"


@pytest.mark.asyncio
async def test_cache_no_corruption_under_load(http_client):
    """50 concurrent requests should not corrupt the pipeline cache."""
    queries = [f"test query {i % 5}" for i in range(50)]

    async def query(q):
        r = await http_client.post("/api/rag/query", json={"query": q, "top_k": 3})
        return r.status_code

    results = await asyncio.gather(*[query(q) for q in queries])
    assert all(r == 200 for r in results), (
        f"Some requests failed: {[r for r in results if r != 200]}"
    )


# ═══════════════════════════════════════════════════════════════════════════ #
# 4A. _get_all_companies caching
# ═══════════════════════════════════════════════════════════════════════════ #
def test_chroma_collection_is_unhashable():
    """Documents why lru_cache cannot key on a Collection.

    ``CollectionCommon`` defines ``__eq__`` without ``__hash__``, so Python sets
    ``__hash__ = None``. Decorating a function that takes a Collection with
    ``@lru_cache`` therefore raises TypeError on the *first* call.
    """
    from chromadb.api.models.Collection import Collection

    assert Collection.__hash__ is None, (
        "chromadb Collection became hashable; the cache-by-name workaround in "
        "data_loader._get_all_companies can be revisited"
    )
    with pytest.raises(TypeError, match="unhashable"):
        hash(FakeCollection())


def test_get_all_companies_is_cached_and_callable():
    """Second call is served from cache, and the first does not raise."""
    collection = FakeCollection(companies=["Acme Corp", "Globex", "Acme Corp"])

    first = data_loader._get_all_companies(collection)
    second = data_loader._get_all_companies(collection)

    assert first == ["Acme Corp", "Globex"]
    assert second == first
    assert collection.get_calls == 1, (
        f"expected 1 Chroma read, got {collection.get_calls}"
    )


def test_get_all_companies_returns_defensive_copy():
    """A caller mutating the result must not poison the cache."""
    collection = FakeCollection(companies=["Acme Corp"])
    first = data_loader._get_all_companies(collection)
    first.append("Injected")

    assert data_loader._get_all_companies(collection) == ["Acme Corp"]


def test_get_all_companies_failure_is_not_cached():
    """A transient Chroma error must not pin an empty list forever."""
    collection = FakeCollection(companies=["Acme Corp"], fail_get=True)
    assert data_loader._get_all_companies(collection) == []

    collection.fail_get = False
    assert data_loader._get_all_companies(collection) == ["Acme Corp"]


def test_get_all_companies_ignores_non_string_company_metadata():
    """Non-string metadata must not reach ``.lower()`` later in resolution."""
    collection = FakeCollection(companies=["Acme Corp", 42, None, ""])
    assert data_loader._get_all_companies(collection) == ["Acme Corp"]


# ═══════════════════════════════════════════════════════════════════════════ #
# 4B. resolve_company_filter caching
# ═══════════════════════════════════════════════════════════════════════════ #
@pytest.fixture
def no_company_index(monkeypatch):
    """Force resolution past the on-disk index so the fallbacks are exercised."""
    monkeypatch.setattr(data_loader, "_load_company_index", lambda: {})


def test_resolve_company_filter_caches_result(no_company_index):
    collection = FakeCollection(peek_companies=["Acme Corp"])

    first = data_loader.resolve_company_filter(collection, "acme")
    second = data_loader.resolve_company_filter(collection, "  ACME  ")

    assert first == {"where": {"company": "Acme Corp"}}
    assert second == first
    assert collection.peek_calls == 1, (
        f"expected 1 peek() call, got {collection.peek_calls}"
    )


def test_resolve_company_filter_returns_defensive_copy(no_company_index):
    collection = FakeCollection(peek_companies=["Acme Corp"])

    first = data_loader.resolve_company_filter(collection, "acme")
    first["where"]["company"] = "TAMPERED"
    del first["where"]

    assert data_loader.resolve_company_filter(collection, "acme") == {
        "where": {"company": "Acme Corp"}
    }


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_resolve_company_filter_blank_input_returns_empty(value, no_company_index):
    """Behaviour preservation: blank input yields {} and caches nothing."""
    collection = FakeCollection(peek_companies=["Acme Corp"])

    assert data_loader.resolve_company_filter(collection, value) == {}
    assert len(data_loader._COMPANY_FILTER_CACHE) == 0
    assert collection.peek_calls == 0


def test_resolve_company_filter_falls_back_to_contains(no_company_index):
    """Behaviour preservation: no match anywhere yields the $contains clause."""
    collection = FakeCollection(companies=["Acme Corp"], peek_companies=[])

    result = data_loader.resolve_company_filter(collection, "Nonexistent Ltd")

    assert result == {"where": {"company": {"$contains": "nonexistent ltd"}}}


def test_resolve_company_filter_uses_dynamic_company_list(no_company_index):
    """peek() misses but the full company scan hits."""
    collection = FakeCollection(companies=["Globex Industries"], peek_companies=[])

    result = data_loader.resolve_company_filter(collection, "globex")

    assert result == {"where": {"company": "Globex Industries"}}


def test_resolve_company_filter_prefers_on_disk_index(monkeypatch):
    monkeypatch.setattr(
        data_loader, "_load_company_index", lambda: {"acme corp": "Acme Corp"}
    )
    collection = FakeCollection(peek_companies=["Wrong Co"])

    assert data_loader.resolve_company_filter(collection, "Acme Corp") == {
        "where": {"company": "Acme Corp"}
    }
    assert collection.peek_calls == 0


def test_company_filter_cache_is_bounded(no_company_index):
    """Arbitrary user input must not grow the cache without limit."""
    collection = FakeCollection(companies=[], peek_companies=[])
    maxsize = data_loader.COMPANY_FILTER_CACHE_MAXSIZE

    for i in range(maxsize * 3):
        data_loader.resolve_company_filter(collection, f"attacker-company-{i}")

    assert len(data_loader._COMPANY_FILTER_CACHE) == maxsize


def test_company_filter_cache_evicts_least_recently_used(no_company_index):
    collection = FakeCollection(companies=[], peek_companies=[])
    cache = data_loader._BoundedCache(2)

    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")          # "a" is now the most recently used
    cache.put("c", 3)       # evicts "b"

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_company_filter_cache_is_thread_safe(no_company_index):
    """Concurrent resolution must stay bounded and never raise."""
    collection = FakeCollection(companies=[], peek_companies=[])
    errors = []

    def worker(offset):
        try:
            for i in range(200):
                data_loader.resolve_company_filter(collection, f"co-{(offset + i) % 400}")
        except Exception as exc:  # noqa: BLE001 - surface any race
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n * 50,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"threads raised: {errors}"
    assert len(data_loader._COMPANY_FILTER_CACHE) <= (
        data_loader.COMPANY_FILTER_CACHE_MAXSIZE
    )


# ═══════════════════════════════════════════════════════════════════════════ #
# 4C. GCS sync retry + artifact verification
# ═══════════════════════════════════════════════════════════════════════════ #
@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Capture data_loader's backoff sleeps instead of actually waiting."""
    sleeps = []
    monkeypatch.setattr(data_loader.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _fake_gsutil(behaviours):
    """Build a subprocess.run double driven by a list of per-attempt actions.

    Each behaviour is either an exception to raise or ``bytes`` to write to the
    destination path.
    """
    calls = {"n": 0}

    def runner(cmd, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        behaviour = behaviours[min(index, len(behaviours) - 1)]
        if isinstance(behaviour, BaseException):
            raise behaviour
        Path(cmd[-1]).write_bytes(behaviour)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    runner.calls = calls
    return runner


def test_chroma_sync_retries_three_times_with_5_10_20_backoff(
    tmp_path, monkeypatch, recorded_sleeps
):
    """Plan 4C: retry 3 times with 5s/10s/20s backoff, then give up."""
    failure = subprocess.CalledProcessError(1, ["gsutil"], stderr=b"503 backend error")
    runner = _fake_gsutil([failure])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)
    tar_path = tmp_path / "chroma_db.tar.gz"

    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        data_loader._download_chroma_archive("gs://bucket/chroma.tar.gz", tar_path)

    assert runner.calls["n"] == 4, f"expected 4 attempts, got {runner.calls['n']}"
    assert recorded_sleeps == [5.0, 10.0, 20.0], recorded_sleeps
    assert not tar_path.exists(), "partial archive left behind"


def test_chroma_sync_recovers_after_transient_failure(
    tmp_path, monkeypatch, recorded_sleeps
):
    good = _make_targz({"vector_db/chroma.sqlite3": b"x" * 64})
    failure = subprocess.CalledProcessError(1, ["gsutil"], stderr=b"connection reset")
    runner = _fake_gsutil([failure, failure, good])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)
    tar_path = tmp_path / "chroma_db.tar.gz"

    data_loader._download_chroma_archive("gs://bucket/chroma.tar.gz", tar_path)

    assert runner.calls["n"] == 3
    assert recorded_sleeps == [5.0, 10.0]
    assert tar_path.exists() and tar_path.stat().st_size > 0


def test_chroma_sync_rejects_empty_artifact(tmp_path, monkeypatch, recorded_sleeps):
    """A 0-byte download must not be treated as a successful sync."""
    runner = _fake_gsutil([b""])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)
    tar_path = tmp_path / "chroma_db.tar.gz"

    with pytest.raises(RuntimeError, match="empty"):
        data_loader._download_chroma_archive("gs://bucket/chroma.tar.gz", tar_path)

    assert runner.calls["n"] == 4
    assert not tar_path.exists()


def test_chroma_sync_rejects_truncated_artifact(tmp_path, monkeypatch, recorded_sleeps):
    """A truncated tar.gz must fail verification, not extraction later on."""
    truncated = _make_targz({"vector_db/chroma.sqlite3": b"y" * 4096})[:40]
    runner = _fake_gsutil([truncated])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)
    tar_path = tmp_path / "chroma_db.tar.gz"

    with pytest.raises(RuntimeError, match="not a readable tar.gz"):
        data_loader._download_chroma_archive("gs://bucket/chroma.tar.gz", tar_path)

    assert not tar_path.exists()


def test_chroma_sync_does_not_retry_missing_gsutil(
    tmp_path, monkeypatch, recorded_sleeps
):
    """gsutil not installed is not transient, so no backoff should be burned."""
    runner = _fake_gsutil([FileNotFoundError("gsutil")])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)

    with pytest.raises(RuntimeError, match="gsutil is required"):
        data_loader._download_chroma_archive(
            "gs://bucket/chroma.tar.gz", tmp_path / "chroma_db.tar.gz"
        )

    assert runner.calls["n"] == 1
    assert recorded_sleeps == []


def test_ensure_chroma_store_end_to_end_after_retry(
    tmp_path, monkeypatch, recorded_sleeps
):
    """Full path: retry, verify, extract, and clean up the archive."""
    good = _make_targz({"vector_db/chroma.sqlite3": b"z" * 128})
    failure = subprocess.CalledProcessError(1, ["gsutil"], stderr=b"timeout")
    runner = _fake_gsutil([failure, good])
    monkeypatch.setattr(data_loader.subprocess, "run", runner)

    chroma_dir = tmp_path / "vector_db"
    result = data_loader.ensure_chroma_store(
        chroma_dir=chroma_dir, remote_uri="gs://bucket/chroma.tar.gz"
    )

    assert result == chroma_dir
    assert (chroma_dir / "chroma.sqlite3").read_bytes() == b"z" * 128
    assert not (tmp_path / "chroma_db.tar.gz").exists(), "archive not cleaned up"
    assert recorded_sleeps == [5.0]


def test_celery_gcs_sync_uses_the_shared_policy():
    """4C (backend side): the rsync is wrapped, not a bare single-shot call."""
    source = TASKS_SOURCE.read_text()

    assert '@resilient(policy="gcs_sync")' in source, (
        "backend/jobs/tasks.py no longer wraps the GCS sync in the gcs_sync policy"
    )
    assert "def sync_data_dir_to_gcs" in source
    assert source.count("sync_data_dir_to_gcs(DATA_DIR, GCS_BUCKET, env)") == 2, (
        "both the add-company and delete-company tasks must use the retrying sync"
    )
    assert "sync_result.returncode" not in source, (
        "an un-retried gsutil rsync call is still present"
    )


def test_gcs_sync_policy_is_3_retries_5_10_20():
    """The policy the decorator resolves must match the plan's numbers."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from utils.resilience import Backoff, get_policy

    policy = get_policy("gcs_sync")
    assert policy.max_retries == 3
    assert policy.backoff is Backoff.EXPONENTIAL
    assert policy.base_delay_s == 5.0
    # sleep_for applies +/-20% jitter around 5s / 10s / 20s.
    for attempt, nominal in ((1, 5.0), (2, 10.0), (3, 20.0)):
        delay = policy.sleep_for(attempt)
        assert nominal * 0.8 <= delay <= nominal * 1.2, (attempt, delay)


# ═══════════════════════════════════════════════════════════════════════════ #
# 4D. Data pipeline checkpointing
# ═══════════════════════════════════════════════════════════════════════════ #
COUNTER_SCRIPT = """\
import os, pathlib, sys
marker = pathlib.Path(os.environ["RUN_COUNTER_FILE"])
with marker.open("a") as fh:
    fh.write("ran\\n")
sys.exit(int(os.environ.get("EXIT_CODE", "0")))
"""


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    """Point pipeline_runner at throwaway scripts/logs/checkpoint dirs."""
    scripts_dir = tmp_path / "scripts"
    logs_dir = tmp_path / "logs"
    checkpoints = tmp_path / ".pipeline_state"
    scripts_dir.mkdir()
    logs_dir.mkdir()

    counter = tmp_path / "runs.txt"
    monkeypatch.setenv("RUN_COUNTER_FILE", str(counter))
    monkeypatch.setattr(pipeline_runner, "SCRIPTS_DIR", str(scripts_dir))
    monkeypatch.setattr(pipeline_runner, "LOGS_DIR", str(logs_dir))
    monkeypatch.setattr(pipeline_runner, "CHECKPOINT_DIR", str(checkpoints))

    for name in ("stage_one.py", "stage_two.py"):
        (scripts_dir / name).write_text(COUNTER_SCRIPT)

    def run_count():
        return len(counter.read_text().splitlines()) if counter.exists() else 0

    return {
        "scripts_dir": scripts_dir,
        "checkpoints": checkpoints,
        "run_count": run_count,
    }


def test_stage_markers_written_on_success(isolated_pipeline):
    _, ok = pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])

    assert ok is True
    assert isolated_pipeline["run_count"]() == 2
    assert pipeline_runner.completed_stages() == [1, 2]
    payload = json.loads(Path(pipeline_runner.marker_path(1)).read_text())
    assert payload["script"] == "stage_one.py"
    assert payload["stage"] == 1


def test_resume_skips_completed_stages(isolated_pipeline):
    pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])
    assert isolated_pipeline["run_count"]() == 2

    _, ok = pipeline_runner.run_pipeline(
        ["stage_one.py", "stage_two.py"], resume=True
    )

    assert ok is True
    assert isolated_pipeline["run_count"]() == 2, "resume re-ran a completed stage"


def test_fresh_run_clears_markers(isolated_pipeline):
    pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])
    assert isolated_pipeline["run_count"]() == 2

    pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])  # resume=False

    assert isolated_pipeline["run_count"]() == 4, "fresh run skipped stages"
    assert pipeline_runner.completed_stages() == [1, 2]


def test_failed_stage_leaves_no_marker_and_reports_failure(
    isolated_pipeline, monkeypatch
):
    monkeypatch.setenv("EXIT_CODE", "1")

    _, ok = pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])

    assert ok is False, "a failing stage must not report success"
    assert pipeline_runner.completed_stages() == []
    assert isolated_pipeline["run_count"]() == 1, "later stages should not run"


def test_resume_restarts_from_the_failed_stage(isolated_pipeline, monkeypatch):
    scripts_dir = isolated_pipeline["scripts_dir"]
    (scripts_dir / "stage_two.py").write_text(
        COUNTER_SCRIPT.replace('os.environ.get("EXIT_CODE", "0")', '"1"')
    )

    _, ok = pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"])
    assert ok is False
    assert pipeline_runner.completed_stages() == [1]
    runs_before = isolated_pipeline["run_count"]()

    # Fix stage two, then resume.
    (scripts_dir / "stage_two.py").write_text(COUNTER_SCRIPT)
    _, ok = pipeline_runner.run_pipeline(["stage_one.py", "stage_two.py"], resume=True)

    assert ok is True
    assert isolated_pipeline["run_count"]() == runs_before + 1, (
        "resume should have run only the previously failed stage"
    )


def test_marker_for_a_different_script_is_not_honoured(isolated_pipeline):
    """Reordering the stage list must not silently skip the wrong work."""
    pipeline_runner.mark_stage_complete(1, "some_other_script.py")

    _, ok = pipeline_runner.run_pipeline(["stage_one.py"], resume=True)

    assert ok is True
    assert isolated_pipeline["run_count"]() == 1


def test_corrupt_marker_is_treated_as_incomplete(isolated_pipeline):
    pipeline_runner.mark_stage_complete(1, "stage_one.py")
    Path(pipeline_runner.marker_path(1)).write_text("{not json")

    assert pipeline_runner.stage_completed(1, "stage_one.py") is False


def test_clear_markers_removes_all(isolated_pipeline):
    pipeline_runner.mark_stage_complete(1, "stage_one.py")
    pipeline_runner.mark_stage_complete(2, "stage_two.py")

    assert pipeline_runner.clear_markers() == 2
    assert pipeline_runner.completed_stages() == []


def test_checkpoint_dir_is_gitignored():
    """Markers are run state and must never reach the repo."""
    probe = Path(pipeline_runner.CHECKPOINT_DIR) / ".stage_1_complete"
    result = subprocess.run(
        ["git", "check-ignore", str(probe)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{probe} is not gitignored (git check-ignore said: {result.stdout or result.stderr})"
    )


def test_pipeline_runner_cli_exposes_resume_and_force():
    assert pipeline_runner._parse_args([]).resume is False
    assert pipeline_runner._parse_args(["--resume"]).resume is True
    assert pipeline_runner._parse_args(["--force"]).resume is False
    with pytest.raises(SystemExit):
        pipeline_runner._parse_args(["--resume", "--force"])


# ═══════════════════════════════════════════════════════════════════════════ #
# 4E. PDF download retry + partial file cleanup
# ═══════════════════════════════════════════════════════════════════════════ #
@pytest.fixture
def download_sleeps(monkeypatch):
    sleeps = []
    monkeypatch.setattr(download_data.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def _fake_get(responses):
    """requests.get double returning/raising one behaviour per attempt."""
    calls = {"n": 0}

    def getter(url, **kwargs):
        index = calls["n"]
        calls["n"] += 1
        behaviour = responses[min(index, len(responses) - 1)]
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    getter.calls = calls
    return getter


PDF_BODY = b"%PDF-1.4 fake handbook body " * 40


def test_pdf_download_retries_then_succeeds(tmp_path, monkeypatch, download_sleeps):
    getter = _fake_get(
        [
            FakeResponse(status_code=503),
            FakeResponse(status_code=502),
            FakeResponse(
                status_code=200,
                body=PDF_BODY,
                headers={"Content-Length": str(len(PDF_BODY))},
            ),
        ]
    )
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    written = download_data.download_with_retry("http://x/a.pdf", target)

    assert written == len(PDF_BODY)
    assert target.read_bytes() == PDF_BODY
    assert getter.calls["n"] == 3
    assert download_sleeps == [2.0, 4.0]
    assert not download_data.tmp_path_for(target).exists()


def test_pdf_download_exhausts_retries_and_cleans_up(
    tmp_path, monkeypatch, download_sleeps
):
    """Plan 4E: 3 retries, and no partial file survives to fool the skip check."""
    getter = _fake_get([FakeResponse(status_code=503)])
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    with pytest.raises(download_data.DownloadError, match="after 4 attempts"):
        download_data.download_with_retry("http://x/a.pdf", target)

    assert getter.calls["n"] == 4
    assert download_sleeps == [2.0, 4.0, 8.0]
    assert not target.exists(), "final path must not exist after total failure"
    assert not download_data.tmp_path_for(target).exists(), "partial .tmp survived"


def test_pdf_download_cleans_up_after_mid_stream_disconnect(
    tmp_path, monkeypatch, download_sleeps
):
    getter = _fake_get(
        [
            FakeResponse(status_code=200, body=PDF_BODY, raise_mid_stream=True),
            FakeResponse(status_code=200, body=PDF_BODY),
        ]
    )
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    download_data.download_with_retry("http://x/a.pdf", target)

    assert target.read_bytes() == PDF_BODY
    assert download_sleeps == [2.0]
    assert not download_data.tmp_path_for(target).exists()


def test_pdf_download_rejects_content_length_mismatch(
    tmp_path, monkeypatch, download_sleeps
):
    getter = _fake_get(
        [FakeResponse(status_code=200, body=b"short", headers={"Content-Length": "9999"})]
    )
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    with pytest.raises(download_data.DownloadError, match="size mismatch"):
        download_data.download_with_retry("http://x/a.pdf", target)

    assert not target.exists()
    assert getter.calls["n"] == 4


def test_pdf_download_ignores_content_length_when_body_is_encoded(
    tmp_path, monkeypatch, download_sleeps
):
    """requests decodes gzip transparently, so the header would false-alarm."""
    getter = _fake_get(
        [
            FakeResponse(
                status_code=200,
                body=PDF_BODY,
                headers={"Content-Length": "17", "Content-Encoding": "gzip"},
            )
        ]
    )
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    assert download_data.download_with_retry("http://x/a.pdf", target) == len(PDF_BODY)
    assert download_sleeps == []


def test_pdf_download_rejects_empty_body(tmp_path, monkeypatch, download_sleeps):
    getter = _fake_get([FakeResponse(status_code=200, body=b"")])
    monkeypatch.setattr(download_data.requests, "get", getter)
    target = tmp_path / "hr_acme.pdf"

    with pytest.raises(download_data.DownloadError):
        download_data.download_with_retry("http://x/a.pdf", target)
    assert not target.exists()


def test_pdf_download_does_not_retry_4xx(tmp_path, monkeypatch, download_sleeps):
    getter = _fake_get([FakeResponse(status_code=404)])
    monkeypatch.setattr(download_data.requests, "get", getter)

    with pytest.raises(download_data.PermanentDownloadError, match="HTTP 404"):
        download_data.download_with_retry("http://x/a.pdf", tmp_path / "a.pdf")

    assert getter.calls["n"] == 1
    assert download_sleeps == []


def test_download_pdf_skips_complete_files_but_replaces_empty_ones(
    tmp_path, monkeypatch, download_sleeps
):
    """The "already exists" check must only trust complete files."""
    save_dir = tmp_path / "raw"
    save_dir.mkdir()
    (save_dir / "hr_done.pdf").write_bytes(b"already complete")
    (save_dir / "hr_empty.pdf").write_bytes(b"")
    # A partial file from a killed run, named like the final artifact + .tmp.
    (save_dir / "hr_empty.pdf.tmp").write_bytes(b"garbage partial")

    urls_path = tmp_path / "url.json"
    urls_path.write_text(
        json.dumps(
            [
                {"domain": "hr", "company": "done", "url": "http://x/done.pdf"},
                {"domain": "hr", "company": "empty", "url": "http://x/empty.pdf"},
            ]
        )
    )

    getter = _fake_get([FakeResponse(status_code=200, body=PDF_BODY)])
    monkeypatch.setattr(download_data.requests, "get", getter)

    download_data.download_pdf(urls_path=urls_path, save_dir=save_dir)

    assert (save_dir / "hr_done.pdf").read_bytes() == b"already complete"
    assert getter.calls["n"] == 1, "the complete file should not have been refetched"
    assert (save_dir / "hr_empty.pdf").read_bytes() == PDF_BODY
    assert not (save_dir / "hr_empty.pdf.tmp").exists()


def test_download_pdf_survives_a_dead_url(tmp_path, monkeypatch, download_sleeps):
    """One bad URL must not abort the whole batch."""
    save_dir = tmp_path / "raw"
    save_dir.mkdir()
    urls_path = tmp_path / "url.json"
    urls_path.write_text(
        json.dumps(
            [
                {"domain": "hr", "company": "dead", "url": "http://x/dead.pdf"},
                {"domain": "hr", "company": "live", "url": "http://x/live.pdf"},
            ]
        )
    )

    getter = _fake_get(
        [FakeResponse(status_code=404), FakeResponse(status_code=200, body=PDF_BODY)]
    )
    monkeypatch.setattr(download_data.requests, "get", getter)

    download_data.download_pdf(urls_path=urls_path, save_dir=save_dir)

    assert not (save_dir / "hr_dead.pdf").exists()
    assert (save_dir / "hr_live.pdf").read_bytes() == PDF_BODY
    assert list(save_dir.glob("*.tmp")) == []


# ═══════════════════════════════════════════════════════════════════════════ #
# 4F. ChromaDB atomic batch writes
# ═══════════════════════════════════════════════════════════════════════════ #
def test_batch_size_is_500():
    assert store_in_chromadb.BATCH_SIZE == 500


def test_dedupe_by_chunk_hash():
    chunks = [
        {"text": "a", "hash": "h1"},
        {"text": "a copy", "hash": "h1"},
        {"text": "b", "hash": "h2"},
        {"text": "c", "hash": " h2 "},
    ]

    unique, dropped = store_in_chromadb.dedupe_chunks(chunks)

    assert dropped == 2
    assert [c["text"] for c in unique] == ["a", "b"]


def test_dedupe_falls_back_to_text_digest_when_hash_missing():
    """Chunks without a hash must not all collide on the empty string."""
    chunks = [
        {"text": "alpha", "hash": ""},
        {"text": "beta", "hash": ""},
        {"text": "alpha"},
    ]

    unique, dropped = store_in_chromadb.dedupe_chunks(chunks)

    assert dropped == 1
    assert [c["text"] for c in unique] == ["alpha", "beta"]


def test_add_in_batches_splits_into_500s():
    collection = RecordingCollection()
    total = 1200
    docs = [f"doc {i}" for i in range(total)]
    metas = [{"i": i} for i in range(total)]
    ids = [f"chunk_{i}" for i in range(total)]

    succeeded = store_in_chromadb.add_in_batches(collection, docs, metas, ids)

    assert collection.batches == [500, 500, 200]
    assert succeeded == [1, 2, 3]


def test_add_in_batches_reports_which_batches_succeeded():
    collection = RecordingCollection(fail_on_batch=3)
    total = 1200
    docs = [f"doc {i}" for i in range(total)]
    metas = [{"i": i} for i in range(total)]
    ids = [f"chunk_{i}" for i in range(total)]

    with pytest.raises(store_in_chromadb.BatchWriteError) as excinfo:
        store_in_chromadb.add_in_batches(collection, docs, metas, ids)

    error = excinfo.value
    assert error.succeeded_batches == [1, 2]
    assert error.written == 1000
    assert "Batch 3/3" in str(error)
    assert "1000 of 1200" in str(error)


def test_add_in_batches_rejects_length_mismatch():
    collection = RecordingCollection()
    with pytest.raises(ValueError, match="length mismatch"):
        store_in_chromadb.add_in_batches(collection, ["a", "b"], [{}], ["1", "2"])
    assert collection.batches == []


def test_add_in_batches_handles_empty_input():
    collection = RecordingCollection()
    assert store_in_chromadb.add_in_batches(collection, [], [], []) == []
    assert collection.batches == []


def test_add_in_batches_rejects_bad_batch_size():
    with pytest.raises(ValueError, match="batch_size must be positive"):
        store_in_chromadb.add_in_batches(
            RecordingCollection(), ["a"], [{}], ["1"], batch_size=0
        )
