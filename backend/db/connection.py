"""
Database connection and session management
"""
import atexit
import logging
import os
from typing import Any, Dict
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool, QueuePool, StaticPool
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Phase 6C: pooling parameters. Cloud Run + Cloud SQL is the production
# target, where every connection costs a TCP + TLS + auth round trip through
# the Cloud SQL connector (tens of ms), so NullPool paid that on every single
# request and a traffic spike opened a new connection per concurrent request
# with no ceiling. QueuePool(5, 10) caps one instance at 15 connections and
# reuses the warm ones.
#
# Sizing note: the ceiling is per *instance*. With Cloud SQL's default
# max_connections, keep `max-instances * (POOL_SIZE + MAX_OVERFLOW)` under it.
# At 15 per instance that is 6 instances against a 100-connection tier, with
# room for admin sessions.
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
# Fail fast when the pool is saturated instead of queueing behind a request
# the user has probably already abandoned. SQLAlchemy's default is 30s.
POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10"))
# Recycle below Cloud SQL's idle-connection reaper so we never hand out a
# socket the server has already closed. pool_pre_ping covers the rest.
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))


def _is_memory_sqlite(url: str) -> bool:
    """True for in-memory SQLite URLs, including shared-cache spellings."""
    if not url.startswith("sqlite"):
        return False
    _, _, tail = url.partition("sqlite")
    path = tail.lstrip(":").lstrip("/")
    return path == "" or path.startswith(":memory:") or "mode=memory" in url


def engine_options(url: str) -> Dict[str, Any]:
    """Pooling configuration for ``url``, branched on dialect.

    Pooling exists to amortise connection setup over a network. SQLite has no
    network, so a pool buys nothing there and costs correctness: SQLite
    serialises writers with a file lock, and a pool that keeps idle
    connections parked across threads turns that into "database is locked".

      - PostgreSQL: QueuePool, the Phase 6C target.
      - In-memory SQLite: StaticPool. The database *is* the connection, so
        every session must share one, otherwise each session sees an empty
        schema. (SQLAlchemy's own default here is SingletonThreadPool, which
        breaks the same way as soon as a second thread appears, which is
        exactly what TestClient does.)
      - File SQLite: NullPool. Connect per checkout, close on release, no
        idle holders of the write lock. This is what SQLAlchemy itself did by
        default before 2.0; 2.0 switched the default to QueuePool, so it is
        now worth stating explicitly.
    """
    if url.startswith("postgresql"):
        return {
            "poolclass": QueuePool,
            "pool_size": POOL_SIZE,
            "max_overflow": MAX_OVERFLOW,
            "pool_timeout": POOL_TIMEOUT,
            "pool_recycle": POOL_RECYCLE,
            "pool_pre_ping": True,   # Verify connections before use
            "echo": False,
        }
    if url.startswith("sqlite"):
        pool = StaticPool if _is_memory_sqlite(url) else NullPool
        # pool_pre_ping is deliberately omitted: NullPool hands out a brand
        # new connection every time, so the extra SELECT 1 is pure overhead.
        return {
            "poolclass": pool,
            "connect_args": {"check_same_thread": False},
            "echo": False,
        }
    # Unknown dialect: pool conservatively but keep the staleness guard.
    logger.warning("Unrecognized database dialect in URL; using default pooling")
    return {"pool_pre_ping": True, "echo": False}

def get_database_url():
    """Auto-detect which database to use based on availability.

    Production supplies DATABASE_URL, so this function is only reached in
    development. If it is reached with ENVIRONMENT=production, that is a
    misconfiguration and it fails loudly rather than falling back to an
    ephemeral SQLite file that would lose every write on container restart.

    A local PostgreSQL is opt-in: set LOCAL_POSTGRES_URL to probe one. There
    is deliberately no built-in default, because a checked-in connection
    string means a checked-in password.
    """
    sqlite_url = "sqlite:///./frontshiftai.db"

    if os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError(
            "DATABASE_URL must be set when ENVIRONMENT=production. Refusing to "
            "fall back to SQLite, which is ephemeral on Cloud Run."
        )

    postgres_url = os.getenv("LOCAL_POSTGRES_URL")
    if not postgres_url:
        logger.info("Using SQLite for local development (set DATABASE_URL to override)")
        return sqlite_url

    try:
        test_engine = create_engine(postgres_url, poolclass=NullPool)
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        test_engine.dispose()
        logger.info("Using local PostgreSQL from LOCAL_POSTGRES_URL")
        return postgres_url
    except Exception as e:
        logger.warning(
            "LOCAL_POSTGRES_URL is set but unreachable (%s), falling back to "
            "SQLite for dev/test", e
        )
        return sqlite_url

# Allow override via environment variable, otherwise auto-detect
DATABASE_URL = os.getenv("DATABASE_URL") or get_database_url()

# Configure engine based on database type (see engine_options docstring)
engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def pool_stats() -> Dict[str, Any]:
    """Snapshot of the live pool, for the Phase 7 gauges and for tests.

    Only reports what the configured pool class actually tracks: NullPool and
    StaticPool have no queue, so ``checkedout`` and friends are absent rather
    than reported as zero, which would read as "idle" on a dashboard.
    """
    pool = engine.pool
    stats: Dict[str, Any] = {"class": type(pool).__name__}
    for name in ("size", "checkedin", "checkedout", "overflow"):
        accessor = getattr(pool, name, None)
        if not callable(accessor):
            continue
        try:
            stats[name] = accessor()
        except Exception:  # noqa: BLE001 - introspection must never raise
            continue
    return stats


# Phase 6C: with a real pool, connections outlive a request, so they need an
# explicit teardown. The FastAPI lifespan does not dispose the engine, and
# Cloud Run SIGTERMs the container, so an atexit hook is the reliable place
# to return pooled connections to Cloud SQL instead of leaving them for the
# server-side idle timeout to reap.
@atexit.register
def _dispose_engine() -> None:
    try:
        engine.dispose()
    except Exception:  # noqa: BLE001 - interpreter is already shutting down
        pass

def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initialize database with all tables"""
    from db import models  # Import models to register them
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created")