"""Phase 5.5: durable LangGraph checkpointing, backed by the app's database.

Until now LangGraph state lived only for the duration of one HTTP request. This
module supplies a ``BaseCheckpointSaver`` so graph state survives the request:
multi-turn conversations can resume, a workflow can pause and be resumed later
(5.5D), and every step of a run is inspectable after the fact.

Why not ``langgraph-checkpoint-postgres``
-----------------------------------------
The Phase 5.5 plan sketched ``PostgresSaver(engine=engine)``. That signature
does not exist in any released version of the library, and the real one is a
poor fit here. Concretely, with ``langgraph==0.2.28`` (which pins
``langgraph-checkpoint<2``) the only compatible releases are
``langgraph-checkpoint-postgres==1.0.9`` and ``langgraph-checkpoint-sqlite==1.0.4``,
and adopting them would mean:

1. **Async savers only.** Both agents drive their graphs with ``ainvoke`` /
   ``astream``. The sync ``PostgresSaver`` and ``SqliteSaver`` raise
   ``NotImplementedError`` from every ``a*`` method, so we would need
   ``AsyncPostgresSaver``, whose ``__init__`` calls
   ``asyncio.get_running_loop()``. A module-level singleton would therefore be
   pinned to whichever event loop happened to construct it, and graph
   compilation (today sync, in ``__init__``) would have to become async.
2. **A second connection pool to Neon.** ``AsyncPostgresSaver`` wants its own
   psycopg3 ``AsyncConnectionPool``, on top of the ``QueuePool(5, 10)`` that
   Phase 6C sized deliberately against the instance connection budget.
3. **Prepared statements against a pgbouncer endpoint.** The library connects
   with ``prepare_threshold=0``. Neon's pooled (``-pooler``) endpoint is
   pgbouncer in transaction mode, where server-side prepared statements break.
   That is a production-only failure we could not reproduce locally.
4. **New runtime dependencies** (``psycopg``, ``psycopg-pool``, ``aiosqlite``,
   plus the two saver packages) for an image whose Dockerfile is frozen.

So this module implements the checkpointer protocol directly on the SQLAlchemy
engine the app already uses. Column layout and semantics are ported from
``langgraph.checkpoint.sqlite.SqliteSaver`` (v1.0.4), so behaviour matches the
reference implementation, but it works on both dialects through one code path,
reuses the existing pool, adds no dependency, and stays loop-agnostic (async
methods hand the blocking work to ``asyncio.to_thread``).

Backend selection
-----------------
``get_checkpointer()`` never raises on a developer machine. It picks, and logs,
one of:

  - **postgresql** -> ``SqlAlchemyCheckpointSaver`` on the app engine. Production.
  - **file sqlite** -> ``SqlAlchemyCheckpointSaver`` on the app engine. Local dev
    is durable too. Safe because file SQLite uses ``NullPool``, so the saver
    checks out its own connection instead of sharing the request's.
  - **in-memory sqlite** -> ``MemorySaver``. The app engine uses ``StaticPool``
    there, meaning one single connection shared by every session; having the
    saver reach for it from a worker thread mid-request invites
    "cannot start a transaction within a transaction". Durability is
    meaningless for a database that dies with the process anyway.
  - **anything else, or any failure during setup** -> ``MemorySaver``, warned
    loudly. Degraded, not dead.

``CHECKPOINTER_BACKEND`` overrides the choice: ``auto`` (default),
``sqlalchemy``, ``memory``, or ``off``. ``off`` returns ``None``, which makes
the agents compile their graphs exactly as they did before Phase 5.5 and is the
kill switch if durable state ever misbehaves in production.

Tenancy (5.5F)
--------------
Every saver handed out is wrapped in :class:`TenantScopedCheckpointSaver`. See
its docstring for the threat model.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.serde.types import ChannelProtocol
from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


# =========================================================================== #
# Thread ids
# =========================================================================== #
#
# A thread id is the durable identity of a conversation's graph state. Format:
#
#     {agent}:{conversation_id}
#
# ``conversation_id`` is a UUID string, so it never contains a colon. Anything
# that does not match that exact two-segment shape (an ad-hoc thread, a
# LangGraph-internal id) is treated as unbound to a conversation. That is safe
# rather than lax: rows are keyed by the *literal* thread id string, so an
# unbound thread can never alias the state of a bound one.

PTO_THREAD_PREFIX = "pto"
HR_THREAD_PREFIX = "hr"
KNOWN_THREAD_PREFIXES = frozenset({PTO_THREAD_PREFIX, HR_THREAD_PREFIX})

# Marks a run with no conversation behind it (the single-agent REST endpoints).
# Such threads are aged out by timestamp instead of by conversation age.
ADHOC_SEGMENT = "adhoc"


def thread_id_for(agent: str, conversation_id: Optional[str]) -> str:
    """Stable thread id for ``agent`` within ``conversation_id``.

    A compiled graph with a checkpointer requires *some* thread id, so callers
    without a conversation (``/api/pto/chat``, ``/api/hr-ticket/chat``) get a
    disposable one rather than being denied durability wholesale.
    """
    if agent not in KNOWN_THREAD_PREFIXES:
        raise ValueError(f"Unknown agent thread prefix: {agent!r}")
    if not conversation_id:
        return f"{agent}:{ADHOC_SEGMENT}:{uuid.uuid4()}"
    return f"{agent}:{conversation_id}"


def conversation_id_from_thread(thread_id: str) -> Optional[str]:
    """The conversation a thread is bound to, or ``None`` if it is unbound.

    Strict on purpose: exactly ``{known agent}:{one more segment}``.
    """
    if not thread_id:
        return None
    parts = thread_id.split(":")
    if len(parts) != 2:
        return None
    agent, conversation_id = parts
    if agent not in KNOWN_THREAD_PREFIXES or not conversation_id:
        return None
    return conversation_id


def thread_ids_for_conversation(conversation_id: str) -> List[str]:
    """Every thread id that could exist for one conversation."""
    return [f"{prefix}:{conversation_id}" for prefix in sorted(KNOWN_THREAD_PREFIXES)]


# =========================================================================== #
# 5.5F Tenant guard
# =========================================================================== #

class CheckpointTenantMismatch(RuntimeError):
    """A checkpoint operation crossed a tenant boundary and was refused."""


# thread_id -> owning company. Ownership of a conversation never changes, so
# this needs no TTL; it is bounded to keep a long-lived process from growing a
# per-conversation entry forever.
_OWNER_CACHE_MAX = 1024
_owner_cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
_owner_cache_lock = threading.Lock()


def reset_owner_cache() -> None:
    """Drop the memoised thread -> company map. For tests."""
    with _owner_cache_lock:
        _owner_cache.clear()


def _cache_get(thread_id: str) -> Tuple[bool, Optional[str]]:
    with _owner_cache_lock:
        if thread_id in _owner_cache:
            _owner_cache.move_to_end(thread_id)
            return True, _owner_cache[thread_id]
    return False, None


def _cache_put(thread_id: str, company: Optional[str]) -> None:
    # Only memoise resolved owners. A ``None`` means "no such conversation
    # (yet)", which can become a real owner a moment later once the row commits.
    if company is None:
        return
    with _owner_cache_lock:
        _owner_cache[thread_id] = company
        _owner_cache.move_to_end(thread_id)
        while len(_owner_cache) > _OWNER_CACHE_MAX:
            _owner_cache.popitem(last=False)


def conversation_owner(conversation_id: str) -> Optional[str]:
    """The company that owns ``conversation_id``, or ``None`` if unknown.

    Uses SQLAlchemy Core, not the ORM ``Query`` API, and that is deliberate.
    The Phase 0.6 ``before_compile`` listener would silently narrow an ORM
    query to the *current* company, so a cross-tenant conversation would come
    back as "not found" and the guard below would wave it through. We need the
    true owner in order to compare, so we read around the auto-filter and do the
    comparison ourselves. ``bypass_tenant_filter()`` would also work but emits
    an audit warning per call, which is far too noisy for a hot path.

    Runs on its own short-lived session so it can never disturb the request's
    transaction or trigger an autoflush of its pending objects.
    """
    from db.connection import SessionLocal
    from db.models import Conversation

    session = SessionLocal()
    try:
        return session.execute(
            select(Conversation.company).where(Conversation.id == conversation_id)
        ).scalar_one_or_none()
    finally:
        session.close()


def verify_thread_access(thread_id: str) -> None:
    """Refuse a checkpoint operation that would cross a tenant boundary.

    Threat model: the chat endpoint accepts a client-supplied
    ``conversation_id`` and the thread id is derived from it. A caller
    authenticated as tenant B who passes tenant A's conversation id would
    otherwise load tenant A's graph state, which carries their email, PTO
    dates and balance.

    Decision table:

    ======================================  ==========================
    situation                               outcome
    ======================================  ==========================
    no tenant context (Celery, CLI, tests)  allow (nothing to cross)
    super admin                             allow, logged
    thread not bound to a conversation      allow (cannot alias a bound thread)
    conversation unknown                    allow (fresh, or not yet committed)
    owner == current company                allow
    owner != current company                **raise**
    lookup failed                           **raise**
    ======================================  ==========================

    The two raising rows are the fail-closed part: whenever a mismatch can be
    established, or when enforcement itself cannot be carried out, the
    operation is refused. This mirrors the philosophy in
    ``db/tenant_context.py``: a 500 beats a cross-tenant read.
    """
    from db.tenant_context import get_current_company, is_super_admin

    company = get_current_company()
    if company is None:
        return

    conversation_id = conversation_id_from_thread(thread_id)
    if conversation_id is None:
        return

    cached, owner = _cache_get(thread_id)
    if not cached:
        try:
            owner = conversation_owner(conversation_id)
        except Exception as exc:  # noqa: BLE001 - this is a security boundary
            logger.error(
                "checkpoint tenant lookup failed for thread=%s, refusing access: %s",
                thread_id,
                exc,
                extra={"audit": True, "event": "checkpoint_tenant_lookup_failure"},
            )
            raise CheckpointTenantMismatch(
                f"Cannot verify tenant ownership of checkpoint thread {thread_id!r}"
            ) from exc
        _cache_put(thread_id, owner)

    if owner is None:
        return

    if owner == company:
        return

    if is_super_admin():
        # Cross-tenant by design (support tooling, monitoring). Still audited.
        logger.warning(
            "super admin reading checkpoint thread=%s owned by company=%s",
            thread_id,
            owner,
            extra={"audit": True, "event": "checkpoint_super_admin_access"},
        )
        return

    logger.error(
        "CROSS-TENANT CHECKPOINT ACCESS BLOCKED: thread=%s owner=%s caller=%s",
        thread_id,
        owner,
        company,
        extra={
            "audit": True,
            "event": "checkpoint_tenant_violation",
            "thread_id": thread_id,
            "owner": owner,
            "caller": company,
        },
    )
    raise CheckpointTenantMismatch(
        f"Checkpoint thread {thread_id!r} belongs to another tenant"
    )


def _thread_id_of(config: Optional[Dict[str, Any]]) -> Optional[str]:
    if not config:
        return None
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    return str(thread_id) if thread_id is not None else None


def _verify_scoped(config: Optional[Dict[str, Any]]) -> None:
    """Guard a listing operation, which may legally have no thread scope.

    ``list``/``alist`` accept ``config=None``, meaning "every thread in the
    store". No caller in this app does that, and LangGraph's own
    ``get_state_history`` always supplies a config, so inside a request it can
    only be a mistake. Refuse it rather than hand back a cross-tenant page.
    """
    from db.tenant_context import get_current_company, is_super_admin

    thread_id = _thread_id_of(config)
    if thread_id is None and get_current_company() is not None and not is_super_admin():
        logger.error(
            "unscoped checkpoint listing refused inside a tenant context",
            extra={"audit": True, "event": "checkpoint_unscoped_listing"},
        )
        raise CheckpointTenantMismatch(
            "Listing checkpoints requires a thread_id inside a tenant context"
        )
    verify_thread_access(thread_id or "")


class TenantScopedCheckpointSaver(BaseCheckpointSaver):
    """Wraps a saver so no operation can touch another tenant's thread.

    The guard lives here rather than in the agents because this is the only
    chokepoint that every path goes through: ``ainvoke``, ``astream``,
    ``aget_state``, ``aget_state_history`` and a future admin resume all reach
    the store through these eight methods.
    """

    def __init__(self, inner: BaseCheckpointSaver) -> None:
        super().__init__(serde=inner.serde)
        self.inner = inner

    # -- introspection ----------------------------------------------------- #

    @property
    def config_specs(self):  # noqa: D102 - delegates
        return self.inner.config_specs

    def get_next_version(self, current: Optional[Any], channel: ChannelProtocol) -> Any:
        return self.inner.get_next_version(current, channel)

    # -- sync -------------------------------------------------------------- #

    def get_tuple(self, config: Dict[str, Any]) -> Optional[CheckpointTuple]:
        verify_thread_access(_thread_id_of(config) or "")
        return self.inner.get_tuple(config)

    def list(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        _verify_scoped(config)
        return self.inner.list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Dict[str, Any]:
        verify_thread_access(_thread_id_of(config) or "")
        return self.inner.put(config, checkpoint, metadata, new_versions)

    def put_writes(
        self, config: Dict[str, Any], writes: Sequence[Tuple[str, Any]], task_id: str
    ) -> None:
        verify_thread_access(_thread_id_of(config) or "")
        return self.inner.put_writes(config, writes, task_id)

    # -- async ------------------------------------------------------------- #

    async def aget_tuple(self, config: Dict[str, Any]) -> Optional[CheckpointTuple]:
        verify_thread_access(_thread_id_of(config) or "")
        return await self.inner.aget_tuple(config)

    async def alist(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        _verify_scoped(config)
        async for item in self.inner.alist(
            config, filter=filter, before=before, limit=limit
        ):
            yield item

    async def aput(
        self,
        config: Dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Dict[str, Any]:
        verify_thread_access(_thread_id_of(config) or "")
        return await self.inner.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self, config: Dict[str, Any], writes: Sequence[Tuple[str, Any]], task_id: str
    ) -> None:
        verify_thread_access(_thread_id_of(config) or "")
        return await self.inner.aput_writes(config, writes, task_id)


# =========================================================================== #
# 5.5A The saver
# =========================================================================== #

# Primary keys, which are what the upserts conflict on.
_CHECKPOINT_PK = ("thread_id", "checkpoint_ns", "checkpoint_id")
_WRITE_PK = ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx")


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """LangGraph checkpointer stored in ``langgraph_checkpoints[_writes]``.

    Ported from ``langgraph.checkpoint.sqlite.SqliteSaver`` v1.0.4: same
    columns, same primary keys, same replace-or-ignore rule in ``put_writes``,
    same ``checkpoint_id DESC`` ordering (checkpoint ids are uuid6, so
    lexicographic order is chronological order), and the same string channel
    versioning so version strings sort correctly as text.

    Differences from the reference:

    - It speaks SQLAlchemy Core, so PostgreSQL and SQLite share one code path.
    - The metadata ``filter`` argument of :meth:`list` is applied in Python
      after deserialisation. The reference pushes it into ``json_extract``,
      which is not portable, and these histories are a handful of rows.
    - The async methods are real: they delegate to the sync ones through
      ``asyncio.to_thread``, so a blocking driver never stalls the loop and
      nothing is bound to a particular event loop.
    """

    def __init__(self, engine, *, serde: Optional[SerializerProtocol] = None) -> None:
        super().__init__(serde=serde)
        self.engine = engine
        self.jsonplus_serde = JsonPlusSerializer()
        self.dialect = engine.dialect.name
        self._setup_done = False
        self._setup_lock = threading.Lock()
        # Resolved lazily so importing this module never touches db.models.
        self._checkpoints = None
        self._writes = None

    # -- schema ------------------------------------------------------------ #

    def setup(self) -> None:
        """Create the two tables if absent. Idempotent, thread safe.

        Called from :func:`get_checkpointer`. It does not rely on
        ``init_db()`` having run, so a fresh production database gets the
        tables the first time a graph executes.
        """
        if self._setup_done:
            return
        with self._setup_lock:
            if self._setup_done:
                return
            from db.models import LangGraphCheckpoint, LangGraphCheckpointWrite

            self._checkpoints = LangGraphCheckpoint.__table__
            self._writes = LangGraphCheckpointWrite.__table__
            self._checkpoints.create(bind=self.engine, checkfirst=True)
            self._writes.create(bind=self.engine, checkfirst=True)
            self._setup_done = True

    def _tables(self):
        self.setup()
        return self._checkpoints, self._writes

    # -- upsert ------------------------------------------------------------ #

    def _upsert(self, conn, table, rows: List[Dict[str, Any]], pk: Sequence[str],
                on_conflict: str) -> None:
        """Insert ``rows``, resolving primary key collisions per ``on_conflict``.

        ``on_conflict`` is ``"replace"`` (SQLite ``INSERT OR REPLACE``) or
        ``"ignore"`` (``INSERT OR IGNORE``). Both dialects we support express
        this natively; the generic branch exists so an unexpected dialect
        degrades to correct-but-slower rather than crashing.
        """
        if not rows:
            return

        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif self.dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            self._upsert_generic(conn, table, rows, pk, on_conflict)
            return

        ins = dialect_insert(table)
        if on_conflict == "replace":
            updatable = [c for c in rows[0] if c not in pk]
            stmt = ins.on_conflict_do_update(
                index_elements=list(pk),
                set_={c: ins.excluded[c] for c in updatable},
            )
        else:
            stmt = ins.on_conflict_do_nothing(index_elements=list(pk))
        conn.execute(stmt, rows)

    def _upsert_generic(self, conn, table, rows, pk, on_conflict) -> None:
        for row in rows:
            match = and_(*[table.c[col] == row[col] for col in pk])
            if on_conflict == "replace":
                conn.execute(delete(table).where(match))
                conn.execute(table.insert().values(**row))
            else:
                try:
                    with conn.begin_nested():
                        conn.execute(table.insert().values(**row))
                except IntegrityError:
                    pass

    # -- reads ------------------------------------------------------------- #

    def _pending_writes(self, conn, thread_id: str, checkpoint_ns: str,
                        checkpoint_id: str) -> List[Tuple[str, str, Any]]:
        writes = self._writes
        rows = conn.execute(
            select(writes.c.task_id, writes.c.channel, writes.c.type, writes.c.value)
            .where(
                writes.c.thread_id == thread_id,
                writes.c.checkpoint_ns == checkpoint_ns,
                writes.c.checkpoint_id == checkpoint_id,
            )
            .order_by(writes.c.task_id, writes.c.idx)
        ).all()
        return [
            (task_id, channel, self.serde.loads_typed((type_, value)))
            for task_id, channel, type_, value in rows
        ]

    def _to_tuple(self, conn, row) -> CheckpointTuple:
        parent = (
            {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.parent_checkpoint_id,
                }
            }
            if row.parent_checkpoint_id
            else None
        )
        return CheckpointTuple(
            {
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            self.serde.loads_typed((row.type, row.checkpoint)),
            self.jsonplus_serde.loads(row.checkpoint_metadata)
            if row.checkpoint_metadata is not None
            else {},
            parent,
            self._pending_writes(
                conn, row.thread_id, row.checkpoint_ns, row.checkpoint_id
            ),
        )

    def get_tuple(self, config: Dict[str, Any]) -> Optional[CheckpointTuple]:
        checkpoints, _ = self._tables()
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        query = select(checkpoints).where(
            checkpoints.c.thread_id == thread_id,
            checkpoints.c.checkpoint_ns == checkpoint_ns,
        )
        if checkpoint_id:
            query = query.where(checkpoints.c.checkpoint_id == checkpoint_id)
        else:
            query = query.order_by(checkpoints.c.checkpoint_id.desc()).limit(1)

        with self.engine.connect() as conn:
            row = conn.execute(query).first()
            if row is None:
                return None
            return self._to_tuple(conn, row)

    def list(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        checkpoints, _ = self._tables()
        query = select(checkpoints)

        if config is not None:
            configurable = config["configurable"]
            query = query.where(
                checkpoints.c.thread_id == str(configurable["thread_id"])
            )
            checkpoint_ns = configurable.get("checkpoint_ns")
            if checkpoint_ns is not None:
                query = query.where(checkpoints.c.checkpoint_ns == checkpoint_ns)
            if checkpoint_id := get_checkpoint_id(config):
                query = query.where(checkpoints.c.checkpoint_id == checkpoint_id)

        if before is not None:
            query = query.where(
                checkpoints.c.checkpoint_id < get_checkpoint_id(before)
            )

        query = query.order_by(checkpoints.c.checkpoint_id.desc())
        # No SQL LIMIT when a metadata filter is in play: filtering happens
        # after deserialisation, so limiting first could drop matching rows.
        if limit is not None and not filter:
            query = query.limit(limit)

        # Materialise inside the connection scope. A generator that yields with
        # the connection still open would hold a pooled connection for as long
        # as the caller takes to iterate.
        with self.engine.connect() as conn:
            rows = conn.execute(query).all()
            tuples = [self._to_tuple(conn, row) for row in rows]

        yielded = 0
        for item in tuples:
            if filter and not all(
                item.metadata.get(key) == value for key, value in filter.items()
            ):
                continue
            yield item
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    # -- writes ------------------------------------------------------------ #

    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Dict[str, Any]:
        checkpoints, _ = self._tables()
        configurable = config["configurable"]
        thread_id = str(configurable["thread_id"])
        checkpoint_ns = configurable["checkpoint_ns"]
        type_, serialized = self.serde.dumps_typed(checkpoint)

        row = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint["id"],
            "parent_checkpoint_id": configurable.get("checkpoint_id"),
            "type": type_,
            "checkpoint": serialized,
            "checkpoint_metadata": self.jsonplus_serde.dumps(metadata),
            "updated_at": datetime.now(timezone.utc),
        }
        with self.engine.begin() as conn:
            self._upsert(conn, checkpoints, [row], _CHECKPOINT_PK, "replace")

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self, config: Dict[str, Any], writes: Sequence[Tuple[str, Any]], task_id: str
    ) -> None:
        if not writes:
            return
        _, writes_table = self._tables()
        configurable = config["configurable"]
        now = datetime.now(timezone.utc)

        rows = []
        for idx, (channel, value) in enumerate(writes):
            type_, serialized = self.serde.dumps_typed(value)
            rows.append(
                {
                    "thread_id": str(configurable["thread_id"]),
                    "checkpoint_ns": str(configurable["checkpoint_ns"]),
                    "checkpoint_id": str(configurable["checkpoint_id"]),
                    "task_id": task_id,
                    "idx": WRITES_IDX_MAP.get(channel, idx),
                    "channel": channel,
                    "type": type_,
                    "value": serialized,
                    "updated_at": now,
                }
            )

        # Reference semantics: special channels (the negative indices in
        # WRITES_IDX_MAP, e.g. an error or interrupt marker) overwrite, because
        # a retry must be able to correct them. Ordinary channel writes are
        # append-once, so a duplicate delivery is ignored rather than rewritten.
        on_conflict = "replace" if all(c in WRITES_IDX_MAP for c, _ in writes) else "ignore"
        with self.engine.begin() as conn:
            self._upsert(conn, writes_table, rows, _WRITE_PK, on_conflict)

    # -- versioning -------------------------------------------------------- #

    def get_next_version(self, current: Optional[str], channel: ChannelProtocol) -> str:
        """Monotonic, zero-padded version string.

        Zero padding matters: versions are compared as strings, so ``"2"`` must
        not sort before ``"10"``. The random suffix is how the reference
        implementation distinguishes two writes at the same integer version.
        """
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"

    # -- async ------------------------------------------------------------- #
    #
    # to_thread rather than a native async driver: the engine is sync, and
    # to_thread copies the current contextvars into the worker, so the Phase
    # 0.6 tenant context (and the request id) survive the hop.

    async def aget_tuple(self, config: Dict[str, Any]) -> Optional[CheckpointTuple]:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for item in items:
            yield item

    async def aput(
        self,
        config: Dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self, config: Dict[str, Any], writes: Sequence[Tuple[str, Any]], task_id: str
    ) -> None:
        return await asyncio.to_thread(self.put_writes, config, writes, task_id)


# =========================================================================== #
# Lazy singleton
# =========================================================================== #

_checkpointer: Optional[BaseCheckpointSaver] = None
_checkpointer_backend: Optional[str] = None
_checkpointer_lock = threading.Lock()
_OFF = "off"


def _select_backend(url: str) -> str:
    configured = os.getenv("CHECKPOINTER_BACKEND", "auto").strip().lower()
    if configured in {"off", "none", "disabled"}:
        return _OFF
    if configured == "memory":
        return "memory"
    if configured == "sqlalchemy":
        return "sqlalchemy"
    if configured not in {"auto", ""}:
        logger.warning(
            "Unrecognised CHECKPOINTER_BACKEND=%r, falling back to auto", configured
        )

    from db.connection import _is_memory_sqlite

    if url.startswith("postgresql"):
        return "sqlalchemy"
    if url.startswith("sqlite"):
        return "memory" if _is_memory_sqlite(url) else "sqlalchemy"
    return "memory"


def get_checkpointer() -> Optional[BaseCheckpointSaver]:
    """The process-wide checkpointer, built on first use.

    Returns ``None`` when checkpointing is switched off, in which case callers
    must compile their graphs without one.
    """
    global _checkpointer, _checkpointer_backend
    if _checkpointer is not None or _checkpointer_backend == _OFF:
        return _checkpointer

    with _checkpointer_lock:
        if _checkpointer is not None or _checkpointer_backend == _OFF:
            return _checkpointer

        from db.connection import DATABASE_URL, engine

        backend = _select_backend(DATABASE_URL)

        if backend == _OFF:
            logger.warning(
                "LangGraph checkpointing disabled by CHECKPOINTER_BACKEND; graph "
                "state will not survive the request"
            )
            _checkpointer_backend = _OFF
            return None

        if backend == "sqlalchemy":
            try:
                saver: BaseCheckpointSaver = SqlAlchemyCheckpointSaver(engine)
                saver.setup()
                logger.info(
                    "LangGraph checkpointing: durable, dialect=%s", engine.dialect.name
                )
            except Exception as exc:  # noqa: BLE001 - must not break a dev machine
                logger.warning(
                    "Could not initialise the durable checkpointer (%s); falling back "
                    "to in-memory checkpoints. State will not survive a restart.",
                    exc,
                )
                backend = "memory"
                saver = MemorySaver()
        else:
            saver = MemorySaver()
            logger.info(
                "LangGraph checkpointing: in-memory (dialect=%s). State does not "
                "survive a restart, which is expected for this database.",
                engine.dialect.name,
            )

        _checkpointer = TenantScopedCheckpointSaver(saver)
        _checkpointer_backend = backend
        return _checkpointer


def checkpointer_backend() -> Optional[str]:
    """Which backend was chosen, or ``None`` if nothing has been built yet."""
    return _checkpointer_backend


def reset_checkpointer() -> None:
    """Forget the singleton so the next call rebuilds it. For tests."""
    global _checkpointer, _checkpointer_backend
    with _checkpointer_lock:
        _checkpointer = None
        _checkpointer_backend = None
    reset_owner_cache()


# =========================================================================== #
# Deletion helpers (used by 5.5E and by DELETE /api/chat/conversations/{id})
# =========================================================================== #

def _delete_threads(thread_ids: Sequence[str]) -> int:
    """Delete every checkpoint and pending write for ``thread_ids``."""
    if not thread_ids:
        return 0

    from db.connection import engine
    from db.models import LangGraphCheckpoint, LangGraphCheckpointWrite

    checkpoints = LangGraphCheckpoint.__table__
    writes = LangGraphCheckpointWrite.__table__

    deleted = 0
    # Chunked because SQLite caps a statement at 999 bound parameters and this
    # list is driven by however many conversations aged out today.
    chunk = 200
    with engine.begin() as conn:
        for start in range(0, len(thread_ids), chunk):
            batch = list(thread_ids[start:start + chunk])
            conn.execute(delete(writes).where(writes.c.thread_id.in_(batch)))
            result = conn.execute(
                delete(checkpoints).where(checkpoints.c.thread_id.in_(batch))
            )
            deleted += result.rowcount or 0
    return deleted


def purge_checkpoints_for_conversations(conversation_ids: Iterable[str]) -> int:
    """Drop the checkpoints belonging to the given conversations.

    Enumerates the exact thread ids per conversation rather than matching on a
    ``LIKE`` pattern, so a conversation id that happens to contain wildcard
    characters cannot widen the delete.
    """
    threads: List[str] = []
    for conversation_id in conversation_ids:
        if conversation_id:
            threads.extend(thread_ids_for_conversation(conversation_id))
    if not threads:
        return 0
    deleted = _delete_threads(threads)
    with _owner_cache_lock:
        for thread_id in threads:
            _owner_cache.pop(thread_id, None)
    return deleted


def purge_checkpoints_older_than(cutoff: datetime) -> int:
    """Drop checkpoints last written before ``cutoff``, whatever their thread.

    Catches the threads no conversation can age out: ad-hoc threads from the
    single-agent REST endpoints, and orphans left behind by a conversation that
    was deleted before this cleanup existed.
    """
    from db.connection import engine
    from db.models import LangGraphCheckpoint, LangGraphCheckpointWrite

    checkpoints = LangGraphCheckpoint.__table__
    writes = LangGraphCheckpointWrite.__table__

    with engine.begin() as conn:
        stale = [
            row[0]
            for row in conn.execute(
                select(checkpoints.c.thread_id)
                .where(checkpoints.c.updated_at < cutoff)
                .distinct()
            ).all()
        ]
        if not stale:
            return 0
        # A thread is only stale if *no* checkpoint in it is recent, otherwise
        # deleting by row would tear a live thread's history in half.
        fresh = {
            row[0]
            for row in conn.execute(
                select(checkpoints.c.thread_id)
                .where(checkpoints.c.updated_at >= cutoff)
                .distinct()
            ).all()
        }
        doomed = [t for t in stale if t not in fresh]
        if not doomed:
            return 0

        deleted = 0
        chunk = 200
        for start in range(0, len(doomed), chunk):
            batch = doomed[start:start + chunk]
            conn.execute(delete(writes).where(writes.c.thread_id.in_(batch)))
            result = conn.execute(
                delete(checkpoints).where(checkpoints.c.thread_id.in_(batch))
            )
            deleted += result.rowcount or 0

    with _owner_cache_lock:
        for thread_id in doomed:
            _owner_cache.pop(thread_id, None)
    return deleted
