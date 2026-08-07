"""Phase 5.5 - Durable LangGraph checkpointing.

What is covered here, and how
-----------------------------
Most of this file is deterministic and runs against a throwaway file-backed
SQLite database, because the interesting parts of 5.5 are not observable from
the outside. A live ``/api/chat/message`` round trip cannot tell you whether
state was durable or whether the tenant guard fired; it can only tell you the
answer looked plausible. So the checkpointer, the tenant guard and the cleanup
task are exercised directly, and the two genuinely end-to-end behaviours
(multi-turn resume, per-thread isolation over HTTP) are the only live tests.

Why a file-backed SQLite rather than the ambient database: the production
saver is ``SqlAlchemyCheckpointSaver``, and ``get_checkpointer()`` deliberately
picks ``MemorySaver`` for an *in-memory* SQLite (see its module docstring), so a
memory database would silently test the wrong backend. The ``checkpoint_env``
fixture therefore builds a real file database and repoints the engine at it.

Deliberately not covered:

- The admin approval endpoint from 5.5D. It does not exist; the plan asks for a
  stub. What *is* covered is the durable half underneath it: a graph that pauses
  at ``wait_for_review``, is written to the database, and is resumed by a
  different saver instance.
- ``api/unified_agent.py``'s HTTP wiring, in-process. Importing that module
  initialises W&B monitoring as a side effect. Its 5.5F ownership check is
  tested through ``conversation_owner()``, the function it calls, plus the live
  cross-tenant test.

Run:
    backend/backend_venv/bin/pytest stress_tests/test_phase5_5_checkpointing.py -v
"""
from __future__ import annotations

import operator
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Dict, List, TypedDict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

# The app imports its own modules as ``db.connection``, ``agents.*`` (backend/
# on sys.path). Use that spelling so this file shares module objects, and hence
# the checkpointer singleton and the tenant ContextVars, with the app.
for _path in (str(REPO_ROOT), str(BACKEND_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _install_torch_stubs() -> None:
    """Stub the two RAG modules that import torch.

    torch is broken in this environment (missing libtorch_cpu.dylib). Nothing
    in this file needs retrieval, but the import graph can reach it.
    """
    if "chat_pipeline.rag.retriever" not in sys.modules:
        retriever = types.ModuleType("chat_pipeline.rag.retriever")
        retriever.bm25_retrieval = lambda *a, **k: []        # type: ignore[attr-defined]
        retriever.vector_retrieval = lambda *a, **k: []      # type: ignore[attr-defined]
        sys.modules["chat_pipeline.rag.retriever"] = retriever
    if "chat_pipeline.rag.reranker" not in sys.modules:
        reranker = types.ModuleType("chat_pipeline.rag.reranker")
        reranker.two_stage_reranker = lambda docs, *a, **k: docs  # type: ignore[attr-defined]
        sys.modules["chat_pipeline.rag.reranker"] = reranker


_install_torch_stubs()

from langgraph.checkpoint.memory import MemorySaver          # noqa: E402
from langgraph.graph import END, StateGraph                  # noqa: E402
from sqlalchemy import create_engine, select, text           # noqa: E402
from sqlalchemy.orm import sessionmaker                      # noqa: E402

import agents.utils.checkpointer as cpm                      # noqa: E402
import db.connection as dbconn                               # noqa: E402
import db.tenant_context as tc                               # noqa: E402
from agents.utils.checkpointer import (                      # noqa: E402
    CheckpointTenantMismatch,
    SqlAlchemyCheckpointSaver,
    TenantScopedCheckpointSaver,
    conversation_id_from_thread,
    conversation_owner,
    get_checkpointer,
    thread_id_for,
    thread_ids_for_conversation,
)
from db.models import Conversation, LangGraphCheckpoint      # noqa: E402


TENANT_A = "acme-corp"
TENANT_B = "globex-inc"


# =========================================================================== #
# Fixtures
# =========================================================================== #

@pytest.fixture
def checkpoint_env(tmp_path, monkeypatch):
    """A throwaway file-backed database, with the app's engine pointed at it.

    Repoints the module attributes rather than the ``DATABASE_URL`` env var:
    ``db.connection`` binds its engine at import time and earlier test modules
    have already imported it, so the env var would arrive far too late.
    Everything under test resolves ``engine`` / ``SessionLocal`` at call time,
    which is what makes the redirect work.
    """
    url = f"sqlite:///{tmp_path / 'checkpoints.db'}"
    engine = create_engine(url, **dbconn.engine_options(url))
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    dbconn.Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(dbconn, "engine", engine)
    monkeypatch.setattr(dbconn, "SessionLocal", Session)
    monkeypatch.setattr(dbconn, "DATABASE_URL", url)

    import jobs.tasks as tasks
    # jobs/tasks.py does ``from db import SessionLocal``, which is a separate
    # binding from the one above.
    monkeypatch.setattr(tasks, "SessionLocal", Session)

    cpm.reset_checkpointer()
    tc.clear_tenant_context()
    try:
        yield types.SimpleNamespace(engine=engine, Session=Session, url=url, tasks=tasks)
    finally:
        cpm.reset_checkpointer()
        tc.clear_tenant_context()
        engine.dispose()


class _CounterState(TypedDict):
    n: int
    trail: Annotated[List[str], operator.add]


def _counter_graph(checkpointer, interrupt_after=None):
    """A tiny graph whose arithmetic makes a wrong resume obvious."""
    builder = StateGraph(_CounterState)
    builder.add_node("first", lambda s: {"n": s["n"] + 1, "trail": ["first"]})
    builder.add_node("second", lambda s: {"n": s["n"] * 10, "trail": ["second"]})
    builder.add_node("third", lambda s: {"n": s["n"] - 3, "trail": ["third"]})
    builder.set_entry_point("first")
    builder.add_edge("first", "second")
    builder.add_edge("second", "third")
    builder.add_edge("third", END)
    kwargs: Dict[str, Any] = {"checkpointer": checkpointer}
    if interrupt_after:
        kwargs["interrupt_after"] = interrupt_after
    return builder.compile(**kwargs)


def _make_conversation(Session, conversation_id: str, company: str, *,
                       updated_at: datetime = None) -> None:
    session = Session()
    try:
        session.add(
            Conversation(
                id=conversation_id,
                email=f"user@{company}.test",
                company=company,
                title="test",
                created_at=datetime.now(timezone.utc),
                updated_at=updated_at or datetime.now(timezone.utc),
            )
        )
        session.commit()
    finally:
        session.close()


def _checkpoint_count(engine, thread_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            select(text("count(*)")).select_from(LangGraphCheckpoint.__table__)
            .where(LangGraphCheckpoint.__table__.c.thread_id == thread_id)
        ).scalar()


# =========================================================================== #
# 5.5A. Thread identity and backend selection
# =========================================================================== #

def test_thread_id_is_derived_not_supplied():
    """A thread id is a pure function of agent + conversation."""
    conversation_id = "11111111-2222-3333-4444-555555555555"
    assert thread_id_for("pto", conversation_id) == f"pto:{conversation_id}"
    assert thread_id_for("hr", conversation_id) == f"hr:{conversation_id}"
    # Stable across calls: this is what makes turn 2 resume turn 1.
    assert thread_id_for("pto", conversation_id) == thread_id_for("pto", conversation_id)
    # And the agents cannot collide with each other.
    assert thread_id_for("pto", conversation_id) != thread_id_for("hr", conversation_id)

    with pytest.raises(ValueError):
        thread_id_for("website_extraction", conversation_id)


def test_adhoc_threads_are_unique_and_unbound():
    """Callers with no conversation still get checkpointing, on a throwaway thread."""
    first = thread_id_for("pto", None)
    second = thread_id_for("pto", None)
    assert first != second
    assert conversation_id_from_thread(first) is None


@pytest.mark.parametrize("thread_id, expected", [
    ("pto:conv-1", "conv-1"),
    ("hr:conv-1", "conv-1"),
    ("pto:adhoc:conv-1", None),      # three segments: not a bound thread
    ("rag:conv-1", None),            # unknown agent prefix
    ("conv-1", None),                # no prefix at all
    ("pto:", None),                  # empty conversation segment
    ("", None),
])
def test_conversation_id_parsing_is_strict(thread_id, expected):
    assert conversation_id_from_thread(thread_id) == expected


def test_backend_choice_per_dialect(monkeypatch):
    """The dialect ladder from the module docstring, asserted."""
    monkeypatch.delenv("CHECKPOINTER_BACKEND", raising=False)
    assert cpm._select_backend("postgresql://u:p@neon.example/db") == "sqlalchemy"
    assert cpm._select_backend("sqlite:///./frontshiftai.db") == "sqlalchemy"
    # In-memory SQLite shares one connection across every session (StaticPool),
    # so the saver must not reach for it mid-request.
    assert cpm._select_backend("sqlite:///:memory:") == "memory"
    assert cpm._select_backend("sqlite://") == "memory"
    # Never a hard failure on an unrecognised dialect.
    assert cpm._select_backend("mysql://u:p@host/db") == "memory"


def test_backend_env_override(monkeypatch):
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "memory")
    assert cpm._select_backend("postgresql://u:p@host/db") == "memory"
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "off")
    assert cpm._select_backend("postgresql://u:p@host/db") == cpm._OFF
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "nonsense")
    # Unrecognised values warn and fall back to auto rather than breaking boot.
    assert cpm._select_backend("postgresql://u:p@host/db") == "sqlalchemy"


def test_checkpointing_can_be_switched_off(checkpoint_env, monkeypatch):
    """``off`` is the kill switch: agents must compile with no checkpointer."""
    monkeypatch.setenv("CHECKPOINTER_BACKEND", "off")
    cpm.reset_checkpointer()
    assert get_checkpointer() is None
    assert cpm.checkpointer_backend() == cpm._OFF


def test_file_database_gets_the_durable_saver(checkpoint_env):
    checkpointer = get_checkpointer()
    assert isinstance(checkpointer, TenantScopedCheckpointSaver)
    assert isinstance(checkpointer.inner, SqlAlchemyCheckpointSaver)
    assert cpm.checkpointer_backend() == "sqlalchemy"
    # setup() is idempotent and does not depend on init_db() having run.
    with checkpoint_env.engine.connect() as conn:
        conn.execute(text("select 1 from langgraph_checkpoints where 1=0"))
        conn.execute(text("select 1 from langgraph_checkpoint_writes where 1=0"))


def test_memory_fallback_when_durable_setup_fails(checkpoint_env, monkeypatch):
    """A broken database must degrade, not crash a developer's machine."""
    def explode(self):
        raise RuntimeError("no such database")

    monkeypatch.setattr(SqlAlchemyCheckpointSaver, "setup", explode)
    cpm.reset_checkpointer()
    checkpointer = get_checkpointer()
    assert isinstance(checkpointer.inner, MemorySaver)
    assert cpm.checkpointer_backend() == "memory"


# =========================================================================== #
# 5.5A. The saver actually satisfies the protocol
# =========================================================================== #

@pytest.mark.asyncio
async def test_state_survives_a_new_saver_instance(checkpoint_env):
    """The point of the phase: graph state outlives the object that wrote it."""
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": "pto:conv-durable"}}

    result = await graph.ainvoke({"n": 1, "trail": []}, config)
    assert result["n"] == 17          # (1 + 1) * 10 - 3
    assert result["trail"] == ["first", "second", "third"]

    # Stand in for a process restart: drop the singleton, rebuild from the same
    # database, and read the state back.
    cpm.reset_checkpointer()
    reloaded = _counter_graph(get_checkpointer())
    snapshot = await reloaded.aget_state(config)
    assert snapshot.values["n"] == 17
    assert snapshot.next == ()


@pytest.mark.asyncio
async def test_threads_do_not_share_state(checkpoint_env):
    """Two conversations must not see each other's channels."""
    graph = _counter_graph(get_checkpointer())
    first = {"configurable": {"thread_id": "pto:conv-one"}}
    second = {"configurable": {"thread_id": "pto:conv-two"}}

    await graph.ainvoke({"n": 1, "trail": []}, first)
    assert (await graph.aget_state(second)).values == {}

    await graph.ainvoke({"n": 5, "trail": []}, second)
    assert (await graph.aget_state(first)).values["n"] == 17
    assert (await graph.aget_state(second)).values["n"] == 57
    assert (await graph.aget_state(first)).values["trail"] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_state_history_is_replayable(checkpoint_env):
    """5.5's third motivation: a run is inspectable after the fact."""
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": "pto:conv-history"}}
    await graph.ainvoke({"n": 1, "trail": []}, config)

    history = [snapshot async for snapshot in graph.aget_state_history(config)]
    # One checkpoint per superstep plus the input checkpoint.
    assert len(history) >= 4
    # Newest first, and the arithmetic is walked backwards through the run.
    assert history[0].values["n"] == 17
    assert [s.values.get("n") for s in history] == [17, 20, 2, 1, None][:len(history)]


@pytest.mark.asyncio
async def test_pause_and_resume_across_instances(checkpoint_env):
    """5.5D foundation: a paused graph is resumable from a cold saver.

    This is the hardest path through the saver, because resuming depends on the
    pending-write rows as well as the checkpoint rows. If this passes, the
    protocol port is right.
    """
    graph = _counter_graph(get_checkpointer(), interrupt_after=["second"])
    config = {"configurable": {"thread_id": "pto:conv-parked"}}

    partial = await graph.ainvoke({"n": 1, "trail": []}, config)
    assert partial["n"] == 20                      # stopped before "third"
    paused = await graph.aget_state(config)
    assert paused.next == ("third",)

    # Cold start, same database.
    cpm.reset_checkpointer()
    resumed_graph = _counter_graph(get_checkpointer(), interrupt_after=["second"])
    assert (await resumed_graph.aget_state(config)).next == ("third",)

    # ``None`` as input means "continue from the checkpoint".
    finished = await resumed_graph.ainvoke(None, config)
    assert finished["n"] == 17
    assert finished["trail"] == ["first", "second", "third"]
    assert (await resumed_graph.aget_state(config)).next == ()


@pytest.mark.asyncio
async def test_update_state_then_resume(checkpoint_env):
    """An external actor can edit parked state before resuming it.

    This is the mechanism a future admin approval endpoint would use to inject
    a decision into a run parked at ``wait_for_review``.
    """
    graph = _counter_graph(get_checkpointer(), interrupt_after=["second"])
    config = {"configurable": {"thread_id": "pto:conv-edited"}}
    await graph.ainvoke({"n": 1, "trail": []}, config)

    await graph.aupdate_state(config, {"n": 100})
    finished = await graph.ainvoke(None, config)
    assert finished["n"] == 97


# =========================================================================== #
# 5.5F. Tenant isolation. The security-critical part.
# =========================================================================== #

@pytest.mark.asyncio
async def test_checkpoint_tenant_isolation(checkpoint_env):
    """Tenant B must not read tenant A's checkpoint via A's conversation id.

    The concrete attack: ``POST /api/chat/message`` takes a client-supplied
    ``conversation_id``, and that id selects the durable thread. A caller
    authenticated as B who passes A's conversation id would otherwise load A's
    graph state, which carries A's employee email, PTO dates and balance.
    """
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)

    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{conversation_id}"}}

    # Tenant A creates state.
    tc.set_tenant_context(company=TENANT_A)
    await graph.ainvoke({"n": 1, "trail": []}, config)
    assert (await graph.aget_state(config)).values["n"] == 17

    # Tenant B points at the same conversation. Every entry point refuses.
    tc.set_tenant_context(company=TENANT_B)
    with pytest.raises(CheckpointTenantMismatch):
        await graph.aget_state(config)
    with pytest.raises(CheckpointTenantMismatch):
        await graph.ainvoke({"n": 1, "trail": []}, config)
    with pytest.raises(CheckpointTenantMismatch):
        [s async for s in graph.aget_state_history(config)]

    # And A is unharmed: B's blocked write did not corrupt or advance the state.
    tc.set_tenant_context(company=TENANT_A)
    assert (await graph.aget_state(config)).values["n"] == 17


@pytest.mark.asyncio
async def test_owner_is_allowed_through(checkpoint_env):
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{conversation_id}"}}

    tc.set_tenant_context(company=TENANT_A)
    assert (await graph.ainvoke({"n": 1, "trail": []}, config))["n"] == 17


@pytest.mark.asyncio
async def test_forged_thread_cannot_alias_a_real_one(checkpoint_env):
    """A thread id that dodges the parser must not reach the victim's rows.

    ``pto:adhoc:<victim>`` is unbound as far as the guard is concerned, so it is
    allowed through. That is safe because rows are keyed by the literal thread
    string, so it addresses a different, empty thread rather than the victim's.
    """
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)

    graph = _counter_graph(get_checkpointer())
    victim = {"configurable": {"thread_id": f"pto:{conversation_id}"}}
    tc.set_tenant_context(company=TENANT_A)
    await graph.ainvoke({"n": 1, "trail": []}, victim)

    tc.set_tenant_context(company=TENANT_B)
    for forged in (
        f"pto:adhoc:{conversation_id}",
        f"rag:{conversation_id}",
        conversation_id,
    ):
        snapshot = await graph.aget_state({"configurable": {"thread_id": forged}})
        assert snapshot.values == {}, f"{forged} reached another tenant's state"


@pytest.mark.asyncio
async def test_guard_fails_closed_when_it_cannot_verify(checkpoint_env, monkeypatch):
    """If ownership cannot be established, refuse rather than allow."""
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{conversation_id}"}}

    def unavailable(_conversation_id):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(cpm, "conversation_owner", unavailable)
    cpm.reset_owner_cache()
    tc.set_tenant_context(company=TENANT_A)
    with pytest.raises(CheckpointTenantMismatch):
        await graph.aget_state(config)


@pytest.mark.asyncio
async def test_unknown_conversation_is_allowed(checkpoint_env):
    """A conversation nobody owns is a fresh thread, not an attack.

    It has to be allowed: the standalone agent routes have no Conversation row
    at all, and the chat route's row may not have committed yet.
    """
    tc.set_tenant_context(company=TENANT_A)
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{uuid.uuid4()}"}}
    assert (await graph.ainvoke({"n": 1, "trail": []}, config))["n"] == 17


@pytest.mark.asyncio
async def test_no_tenant_context_is_allowed(checkpoint_env):
    """Background work (Celery, CLI) runs outside a request and must not break."""
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)
    tc.clear_tenant_context()
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{conversation_id}"}}
    assert (await graph.ainvoke({"n": 1, "trail": []}, config))["n"] == 17


@pytest.mark.asyncio
async def test_super_admin_may_read_across_tenants(checkpoint_env):
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)
    graph = _counter_graph(get_checkpointer())
    config = {"configurable": {"thread_id": f"pto:{conversation_id}"}}

    tc.set_tenant_context(company=TENANT_A)
    await graph.ainvoke({"n": 1, "trail": []}, config)

    tc.set_tenant_context(company=TENANT_B, is_super_admin=True)
    assert (await graph.aget_state(config)).values["n"] == 17


def test_conversation_owner_reads_around_the_tenant_filter(checkpoint_env):
    """The lookup behind both 5.5F guards must see the *true* owner.

    An ORM query here would be narrowed to the caller's company by the Phase
    0.6 ``before_compile`` listener, so another tenant's conversation would come
    back as "not found" and the guard would wave it through. This is why
    ``conversation_owner`` uses Core.
    """
    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)

    tc.set_tenant_context(company=TENANT_B)
    assert conversation_owner(conversation_id) == TENANT_A
    assert conversation_owner(str(uuid.uuid4())) is None

    # Contrast: the ORM path, which is what a naive check would have used.
    session = checkpoint_env.Session()
    try:
        assert session.query(Conversation).filter(
            Conversation.id == conversation_id
        ).first() is None
    finally:
        session.close()


# =========================================================================== #
# 5.5E. Cleanup
# =========================================================================== #

@pytest.mark.asyncio
async def test_checkpoint_cleanup_old_conversations(checkpoint_env):
    """Dormant conversations lose their checkpoints; active ones keep theirs."""
    stale_id = str(uuid.uuid4())
    active_id = str(uuid.uuid4())
    _make_conversation(
        checkpoint_env.Session, stale_id, TENANT_A,
        updated_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    _make_conversation(checkpoint_env.Session, active_id, TENANT_A)

    graph = _counter_graph(get_checkpointer())
    for conversation_id in (stale_id, active_id):
        await graph.ainvoke(
            {"n": 1, "trail": []},
            {"configurable": {"thread_id": f"pto:{conversation_id}"}},
        )

    assert _checkpoint_count(checkpoint_env.engine, f"pto:{stale_id}") > 0
    assert _checkpoint_count(checkpoint_env.engine, f"pto:{active_id}") > 0

    report = checkpoint_env.tasks.cleanup_stale_checkpoints(retention_days=30)

    assert report["stale_conversations"] == 1
    assert report["deleted_by_conversation"] > 0
    assert _checkpoint_count(checkpoint_env.engine, f"pto:{stale_id}") == 0
    assert _checkpoint_count(checkpoint_env.engine, f"pto:{active_id}") > 0


@pytest.mark.asyncio
async def test_cleanup_removes_orphaned_and_adhoc_threads(checkpoint_env):
    """Threads with no conversation to age out are expired by timestamp."""
    adhoc_thread = thread_id_for("pto", None)
    graph = _counter_graph(get_checkpointer())
    await graph.ainvoke(
        {"n": 1, "trail": []}, {"configurable": {"thread_id": adhoc_thread}}
    )
    fresh_thread = thread_id_for("pto", None)
    await graph.ainvoke(
        {"n": 1, "trail": []}, {"configurable": {"thread_id": fresh_thread}}
    )

    # Backdate only the first thread's rows.
    old = datetime.now(timezone.utc) - timedelta(days=90)
    with checkpoint_env.engine.begin() as conn:
        conn.execute(
            text("update langgraph_checkpoints set updated_at = :ts "
                 "where thread_id = :tid"),
            {"ts": old, "tid": adhoc_thread},
        )

    report = checkpoint_env.tasks.cleanup_stale_checkpoints(retention_days=30)

    assert report["deleted_orphaned"] > 0
    assert _checkpoint_count(checkpoint_env.engine, adhoc_thread) == 0
    assert _checkpoint_count(checkpoint_env.engine, fresh_thread) > 0


@pytest.mark.asyncio
async def test_cleanup_does_not_tear_a_partly_recent_thread(checkpoint_env):
    """A thread with any recent checkpoint is left entirely alone.

    Deleting per row would leave a live thread with holes in its history, which
    is worse than keeping a few stale rows.
    """
    thread = thread_id_for("pto", None)
    graph = _counter_graph(get_checkpointer())
    await graph.ainvoke(
        {"n": 1, "trail": []}, {"configurable": {"thread_id": thread}}
    )
    before = _checkpoint_count(checkpoint_env.engine, thread)
    assert before >= 2

    # Age out all but the newest row.
    with checkpoint_env.engine.begin() as conn:
        conn.execute(
            text("update langgraph_checkpoints set updated_at = :ts "
                 "where thread_id = :tid and checkpoint_id != "
                 "(select max(checkpoint_id) from langgraph_checkpoints "
                 " where thread_id = :tid)"),
            {"ts": datetime.now(timezone.utc) - timedelta(days=90), "tid": thread},
        )

    checkpoint_env.tasks.cleanup_stale_checkpoints(retention_days=30)
    assert _checkpoint_count(checkpoint_env.engine, thread) == before


@pytest.mark.asyncio
async def test_purge_by_conversation_enumerates_exact_threads(checkpoint_env):
    """No LIKE patterns: a wildcard in an id must not widen the delete."""
    target = "50%"          # a valid string id that is also a LIKE wildcard
    bystander = "50-other"
    graph = _counter_graph(get_checkpointer())
    for conversation_id in (target, bystander):
        for agent in ("pto", "hr"):
            await graph.ainvoke(
                {"n": 1, "trail": []},
                {"configurable": {"thread_id": f"{agent}:{conversation_id}"}},
            )

    assert thread_ids_for_conversation(target) == [f"hr:{target}", f"pto:{target}"]
    cpm.purge_checkpoints_for_conversations([target])

    assert _checkpoint_count(checkpoint_env.engine, f"pto:{target}") == 0
    assert _checkpoint_count(checkpoint_env.engine, f"hr:{target}") == 0
    assert _checkpoint_count(checkpoint_env.engine, f"pto:{bystander}") > 0
    assert _checkpoint_count(checkpoint_env.engine, f"hr:{bystander}") > 0


def test_cleanup_is_a_noop_on_an_empty_database(checkpoint_env):
    report = checkpoint_env.tasks.cleanup_stale_checkpoints(retention_days=30)
    assert report == {
        "retention_days": 30,
        "stale_conversations": 0,
        "deleted_by_conversation": 0,
        "deleted_orphaned": 0,
    }


def test_cleanup_task_is_on_the_beat_schedule():
    from jobs.worker import celery_app
    entry = celery_app.conf.beat_schedule["cleanup-stale-checkpoints"]
    assert entry["task"] == "jobs.tasks.cleanup_stale_checkpoints"


# =========================================================================== #
# 5.5B. Agent wiring
# =========================================================================== #

def test_pto_state_channels_do_not_accumulate():
    """Regression guard for the reducer that made durable state explode.

    ``validation_errors`` and friends carried ``operator.add``, while every node
    in ``agents/pto/nodes.py`` mutates the state it is handed and returns the
    whole thing. LangGraph folded the full list into the channel that already
    held it. Measured on the real pattern: six entries from two appends in a
    single run, then 6 / 30 / 126 / 510 over four turns once a stable thread
    made the previous turn's list the starting point.
    """
    from agents.pto.state import PTOAgentState

    builder = StateGraph(PTOAgentState)

    def append_one(state):
        state["validation_errors"].append("first problem")
        return state              # whole snapshot, exactly like the real nodes

    def append_two(state):
        state["validation_errors"].append("second problem")
        return state

    builder.add_node("one", append_one)
    builder.add_node("two", append_two)
    builder.set_entry_point("one")
    builder.add_edge("one", "two")
    builder.add_edge("two", END)
    graph = builder.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "pto:reducer-check"}}
    for turn in range(4):
        result = graph.invoke(
            {"user_message": f"turn {turn}", "validation_errors": []}, config
        )
        assert result["validation_errors"] == ["first problem", "second problem"], (
            f"turn {turn} accumulated: {result['validation_errors']}"
        )


def test_pto_agent_compiles_with_the_checkpointer(checkpoint_env):
    from agents.pto.agent import PTOAgent

    agent = PTOAgent(db=checkpoint_env.Session())
    assert isinstance(agent.checkpointer, TenantScopedCheckpointSaver)
    assert agent.graph.checkpointer is agent.checkpointer

    conversation_id = str(uuid.uuid4())
    config = agent.thread_config(conversation_id)
    assert config["configurable"]["thread_id"] == f"pto:{conversation_id}"
    # No conversation still yields a usable, disposable thread.
    assert conversation_id_from_thread(
        agent.thread_config(None)["configurable"]["thread_id"]
    ) is None


@pytest.mark.asyncio
async def test_pto_review_suspend_is_off_by_default(checkpoint_env, monkeypatch):
    """5.5D is a stub: the default graph has no wait_for_review node."""
    from agents.pto.agent import PTOAgent, review_suspend_enabled

    monkeypatch.delenv("PTO_REVIEW_SUSPEND", raising=False)
    assert review_suspend_enabled() is False
    agent = PTOAgent(db=checkpoint_env.Session())
    assert "wait_for_review" not in agent.graph.nodes

    monkeypatch.setenv("PTO_REVIEW_SUSPEND", "true")
    assert review_suspend_enabled() is True
    suspending = PTOAgent(db=checkpoint_env.Session())
    assert "wait_for_review" in suspending.graph.nodes
    assert "wait_for_review" in suspending.graph.interrupt_after_nodes


@pytest.mark.asyncio
async def test_pto_review_pause_and_resume_end_to_end(checkpoint_env, monkeypatch):
    """5.5D: the real PTO graph parks at wait_for_review and resumes later.

    Everything except the node bodies is production code: the graph, the
    interrupt, the durable saver, ``resume_after_review``. The nodes are stubbed
    only because the real ones call an LLM and need a seeded PTO balance.

    ``request_created`` is already True at the pause, which is the point: the
    request row exists and the user-facing response has not been generated yet,
    so an approval decision can still be folded in before the user hears back.
    """
    import agents.pto.agent as pto_module
    from agents.pto.agent import PTOAgent

    monkeypatch.setenv("PTO_REVIEW_SUSPEND", "true")

    visited: List[str] = []

    def node(name, **updates):
        def run(state, db):
            visited.append(name)
            state.update(updates)
            return state
        return run

    monkeypatch.setattr(pto_module, "parse_intent_node",
                        node("parse_intent", intent="request_pto"))
    monkeypatch.setattr(pto_module, "validate_dates_node",
                        node("validate_dates", is_valid=True, total_business_days=2.0))
    monkeypatch.setattr(pto_module, "check_balance_node",
                        node("check_balance", has_sufficient_balance=True,
                             remaining_days=8.0))
    monkeypatch.setattr(pto_module, "check_conflicts_node",
                        node("check_conflicts", has_conflicts=False))
    monkeypatch.setattr(pto_module, "create_request_node",
                        node("create_request", request_created=True, request_id="PTO-1"))
    monkeypatch.setattr(pto_module, "generate_response_node",
                        node("generate_response", agent_response="Approved and filed."))

    conversation_id = str(uuid.uuid4())
    _make_conversation(checkpoint_env.Session, conversation_id, TENANT_A)
    tc.set_tenant_context(company=TENANT_A)

    agent = PTOAgent(db=checkpoint_env.Session())
    parked = await agent.execute(
        user_email=f"user@{TENANT_A}.test", company=TENANT_A,
        message="two days off next week", conversation_id=conversation_id,
    )

    # wait_for_review is the real node, so it does not appear in ``visited``;
    # ``awaiting_review`` is its observable effect.
    assert visited == ["parse_intent", "validate_dates", "check_balance",
                       "check_conflicts", "create_request"]
    assert parked["awaiting_review"] is True
    assert parked["request_created"] is True
    assert parked["response"] == ""          # no answer generated yet

    config = agent.thread_config(conversation_id)
    assert (await agent.graph.aget_state(config)).next == ("generate_response",)

    # A different process picks it up: new singleton, new agent, same database.
    cpm.reset_checkpointer()
    resumer = PTOAgent(db=checkpoint_env.Session())
    resumed = await resumer.resume_after_review(conversation_id)

    assert visited[-1] == "generate_response"
    assert resumed["response"] == "Approved and filed."
    assert resumed["request_id"] == "PTO-1"
    assert (await resumer.graph.aget_state(config)).next == ()


@pytest.mark.asyncio
async def test_pto_carry_over_decision_table(checkpoint_env):
    """5.5C: which slots survive into the next turn, and which must not."""
    from agents.pto.agent import PTOAgent

    agent = PTOAgent(db=checkpoint_env.Session())

    class _Snapshot:
        def __init__(self, values):
            self.values = values

    async def snapshot_of(values):
        agent.graph.aget_state = lambda config: _wrap(values)  # type: ignore[assignment]
        return await agent._carry_over({"configurable": {"thread_id": "pto:x"}})

    async def _wrap(values):
        return _Snapshot(values)

    mid_request = {
        "intent": "request_pto",
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
        "reason": "family trip",
        "request_created": False,
    }
    assert await snapshot_of(mid_request) == {
        "start_date": "2026-09-01",
        "end_date": "2026-09-02",
        "reason": "family trip",
    }

    # A finished request must not be silently resubmitted by a later turn.
    assert await snapshot_of({**mid_request, "request_created": True}) == {}
    # Nor should a run parked for review be treated as a slot-filling session.
    assert await snapshot_of({**mid_request, "awaiting_review": True}) == {}
    # A different intent is a new topic, not a continuation.
    assert await snapshot_of({**mid_request, "intent": "check_balance"}) == {}
    # Nothing to carry.
    assert await snapshot_of({}) == {}


@pytest.mark.asyncio
async def test_pto_turn_input_omits_only_carried_slots(checkpoint_env):
    """Omission is what preserves a slot; ``None`` would overwrite it."""
    from agents.pto.agent import PTOAgent

    agent = PTOAgent(db=checkpoint_env.Session())

    async def carry(_config):
        return {"start_date": "2026-09-01", "reason": "family trip"}

    agent._carry_over = carry  # type: ignore[assignment]
    turn = await agent._turn_input("u@a.test", TENANT_A, "next Thursday", {})

    assert "start_date" not in turn and "reason" not in turn
    # Everything else is restated, so no stale flag or balance leaks forward.
    assert turn["end_date"] is None
    assert turn["request_created"] is False
    assert turn["awaiting_review"] is False
    assert turn["agent_response"] == ""
    assert turn["validation_errors"] == []
    assert turn["user_message"] == "next Thursday"


@pytest.mark.asyncio
async def test_hr_agent_runs_its_compiled_graph(checkpoint_env, monkeypatch):
    """Regression: HR's graph used to be dead code.

    Before Phase 5.5B, ``HRTicketAgent`` compiled a graph in ``__init__`` and
    then hand-executed the nodes instead, because the graph was built with bare
    one-argument node references. Invoking it raised ``TypeError:
    parse_intent_node() missing 1 required positional argument: 'db'``, so a
    checkpointer attached to it would have recorded nothing. This asserts the
    graph is what runs, that ``db`` reaches every node, and that the run lands
    in the checkpoint table.
    """
    import agents.hr_ticket.agent as hr_module
    from agents.hr_ticket.agent import HRTicketAgent

    visited: List[str] = []

    def node(name, **updates):
        def run(state, db):
            assert db is not None, f"{name} did not receive the session"
            visited.append(name)
            state.update(updates)
            return state
        return run

    monkeypatch.setattr(hr_module, "parse_intent_node",
                        node("parse_intent", intent="create_ticket"))
    monkeypatch.setattr(hr_module, "validate_request_node",
                        node("validate_request", is_valid=True))
    monkeypatch.setattr(hr_module, "check_duplicates_node",
                        node("check_duplicates", has_open_tickets=False))
    monkeypatch.setattr(hr_module, "create_ticket_node",
                        node("create_ticket", ticket_id="TCK-1", queue_position=3))
    monkeypatch.setattr(hr_module, "generate_response_node",
                        node("generate_response", agent_response="Ticket filed."))

    agent = HRTicketAgent()
    conversation_id = str(uuid.uuid4())
    result = await agent.process_message(
        user_email="u@a.test", company=TENANT_A, message="I need a meeting",
        db=checkpoint_env.Session(), conversation_id=conversation_id,
    )

    assert visited == ["parse_intent", "validate_request", "check_duplicates",
                       "create_ticket", "generate_response"]
    assert result == {
        "response": "Ticket filed.",
        "ticket_created": True,
        "ticket_id": "TCK-1",
        "queue_position": 3,
        "has_open_tickets": False,
        "open_ticket_ids": [],
    }
    assert _checkpoint_count(checkpoint_env.engine, f"hr:{conversation_id}") > 0


@pytest.mark.asyncio
async def test_hr_invalid_request_skips_ticket_creation(checkpoint_env, monkeypatch):
    """The graph's conditional edge reproduces the old manual ``if is_valid``."""
    import agents.hr_ticket.agent as hr_module
    from agents.hr_ticket.agent import HRTicketAgent

    visited: List[str] = []

    def node(name, **updates):
        def run(state, db):
            visited.append(name)
            state.update(updates)
            return state
        return run

    monkeypatch.setattr(hr_module, "parse_intent_node", node("parse_intent"))
    monkeypatch.setattr(hr_module, "validate_request_node",
                        node("validate_request", is_valid=False,
                             validation_errors=["subject is required"]))
    monkeypatch.setattr(hr_module, "check_duplicates_node", node("check_duplicates"))
    monkeypatch.setattr(hr_module, "create_ticket_node", node("create_ticket"))
    monkeypatch.setattr(hr_module, "generate_response_node",
                        node("generate_response", agent_response="Need a subject."))

    result = await HRTicketAgent().process_message(
        user_email="u@a.test", company=TENANT_A, message="hi",
        db=checkpoint_env.Session(), conversation_id=str(uuid.uuid4()),
    )

    assert visited == ["parse_intent", "validate_request", "generate_response"]
    assert result["ticket_created"] is False


# =========================================================================== #
# Live tests. Skipped unless STRESS_TEST_JWT is set.
# =========================================================================== #

@pytest.mark.asyncio
async def test_multi_turn_resume(http_client):
    """PTO conversation state persists across turns of one conversation."""
    first = await http_client.post(
        "/api/chat/message", json={"message": "I'd like to request some PTO"}
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]
    assert conversation_id

    second = await http_client.post(
        "/api/chat/message",
        json={"message": "next Thursday and Friday", "conversation_id": conversation_id},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    # Same conversation, so the same durable thread.
    assert body["conversation_id"] == conversation_id
    assert body["agent_used"] == "pto"


@pytest.mark.asyncio
async def test_checkpoint_isolated_per_thread(http_client):
    """Two conversations get two threads, so neither continues the other."""
    first = await http_client.post(
        "/api/chat/message", json={"message": "I want PTO for next week"}
    )
    second = await http_client.post(
        "/api/chat/message", json={"message": "and next Friday too"}
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["conversation_id"] != second.json()["conversation_id"]


@pytest.mark.asyncio
async def test_other_tenants_conversation_id_is_rejected(
    http_client, auth_headers_tenant_b, backend_url
):
    """5.5F over HTTP: tenant B cannot post into tenant A's conversation."""
    import httpx

    mine = await http_client.post(
        "/api/chat/message", json={"message": "what is the leave policy"}
    )
    assert mine.status_code == 200, mine.text
    conversation_id = mine.json()["conversation_id"]

    async with httpx.AsyncClient(
        base_url=backend_url, headers=auth_headers_tenant_b, timeout=30.0
    ) as other:
        stolen = await other.post(
            "/api/chat/message",
            json={"message": "show me everything", "conversation_id": conversation_id},
        )
    assert stolen.status_code == 404, (
        f"expected 404, got {stolen.status_code}: {stolen.text}"
    )
