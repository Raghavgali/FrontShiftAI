"""
FrontShiftAI FastAPI Application
Main entry point for the backend API
"""
import os
import sys
import logging
import warnings
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import uvicorn
import asyncio


# Silence noisy upstream deprecation warnings we can't fix at our layer.
# langgraph's checkpoint.base imports trigger a LangChainPendingDeprecationWarning
# about ``allowed_objects`` from JsonPlusSerializer — internal to the lib;
# our code never touches that serializer directly.
warnings.filterwarnings(
    "ignore",
    message=r".*allowed_objects.*will change in a future version.*",
)

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger(__name__)

# Ensure project root is in Python path
current_file = Path(__file__).resolve()
project_root = current_file.parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import routers
from api import auth, rag, admin, health
from api.unified_agent import router as unified_agent_router
from api.pto_agent import router as pto_router
from api.hr_ticket_agent import router as hr_ticket_router
from api.company_management import router as company_management_router

# Import monitoring middleware
from monitoring.middleware import MonitoringMiddleware

# Tenant context (Phase 0.6)
from db.tenant_context import set_tenant_context, clear_tenant_context
from api.auth import decode_access_token

# Observability (Phase 7)
from observability.metrics import prometheus_middleware, metrics_endpoint
from observability.tracing import request_id_middleware, install_log_filter

# Import ChromaDB setup from chat_pipeline
from chat_pipeline.rag.data_loader import ensure_chroma_store, get_collection, _embedding_function
import time


def _validate_chromadb_tenant_labels(collection, sample_size: int = 1000) -> None:
    """Fail startup if any sampled ChromaDB chunk lacks a ``company`` label.

    Phase 0.6E: a data-pipeline bug that ingests unlabeled chunks would
    otherwise leak into RAG responses. Caught at boot, not at query time.
    """
    try:
        sample = collection.get(include=["metadatas"], limit=sample_size)
    except Exception as exc:
        raise RuntimeError(f"ChromaDB tenant-label validator: unable to sample collection: {exc}") from exc

    metadatas = sample.get("metadatas") or []
    for meta in metadatas:
        company = (meta or {}).get("company")
        if not company or not isinstance(company, str) or not company.strip():
            raise RuntimeError(
                "ChromaDB tenant-label validator: found chunk with missing/empty "
                f"'company' metadata (sample meta={meta!r}). Refusing to start."
            )

# Phase 3D: active-request counter, maintained by middleware and read by
# the lifespan shutdown drain. Single event loop, so a plain int is safe.
_inflight_requests = 0

# ----------------------------
# Lifespan Events
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 FrontShiftAI API starting up...")

    # Ensure ChromaDB is available in production
    if os.getenv("ENVIRONMENT") == "production":
        try:
            logger.info("Ensuring ChromaDB is available...")
            ensure_chroma_store()
            logger.info("ChromaDB ready")
        except Exception as e:
            logger.critical(f"Startup failed — ChromaDB unavailable: {e}")
            raise

    # Initialize database
    from db import init_db
    from db.seed import seed_initial_data
    init_db()
    seed_initial_data()

    # ----------------------------
    # WARMUP: Preload RAG Models
    # ----------------------------
    print("🔥 Warming up RAG models...")
    try:
        warmup_start = time.time()

        # 1. Preload embedding model (5-15s on cold start)
        print("⏳ Loading embedding model...")
        embedding_fn = _embedding_function()
        print("✅ Embedding model loaded")

        # 2. Preload ChromaDB collection (2-5s on cold start)
        print("⏳ Loading ChromaDB collection...")
        collection = get_collection()
        doc_count = collection.count()
        print(f"✅ ChromaDB collection loaded ({doc_count} documents)")

        # Phase 0.6E: validate every sampled chunk carries a company label.
        print("⏳ Validating ChromaDB tenant labels...")
        _validate_chromadb_tenant_labels(collection)
        print("✅ ChromaDB tenant labels validated")

        # 3. Preload reranker model if enabled (3-10s on cold start)
        try:
            from chat_pipeline.rag.config_manager import load_rag_config
            rag_config = load_rag_config()
            if rag_config.get("pipeline", {}).get("reranker", {}).get("enabled", False):
                print("⏳ Loading reranker model...")
                from chat_pipeline.rag.reranker import _get_cross_encoder
                reranker = _get_cross_encoder()
                print("✅ Reranker model loaded")
            else:
                print("ℹ️  Reranker disabled - skipping")
        except Exception as e:
            print(f"⚠️  Reranker preload skipped: {e}")

        warmup_duration = time.time() - warmup_start
        print(f"🔥 Warmup complete in {warmup_duration:.2f}s - Ready for requests!")

    except Exception as e:
        logger.critical(f"Startup failed — RAG warmup error: {e}", exc_info=True)
        if os.getenv("ENVIRONMENT") == "production":
            raise
        print("⚠️  Warmup failed (non-production) — service will continue but first request may be slow")

    yield

    # Phase 3D: drain in-flight requests before the process exits. Waits
    # only as long as traffic is actually outstanding, capped at 10s so a
    # stuck stream can't block the deploy.
    logger.info("Shutting down - draining in-flight requests")
    deadline = time.time() + 10
    while _inflight_requests > 0 and time.time() < deadline:
        await asyncio.sleep(0.1)
    if _inflight_requests > 0:
        logger.warning(
            f"Shutdown proceeding with {_inflight_requests} requests still in flight"
        )
    else:
        logger.info("All in-flight requests drained")

# ----------------------------
# FASTAPI APP
# ----------------------------
app = FastAPI(
    title="FrontShiftAI API",
    version="2.1.0",
    description="Multi-company RAG system with unified AI agents",
    lifespan=lifespan
)


@app.middleware("http")
async def _track_inflight_requests(request: Request, call_next):
    """Phase 3D: count active requests so shutdown can drain them."""
    global _inflight_requests
    _inflight_requests += 1
    try:
        return await call_next(request)
    finally:
        _inflight_requests -= 1

# ----------------------------
# ERROR HANDLING
# ----------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler to catch all unhandled errors.
    Logs the full error and returns a generic user-friendly message.
    """
    # Log the detailed error
    logger.error(f"Unhandled exception at {request.url}: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": "We're experiencing technical difficulties. Please try again later.",
            "error_code": "INTERNAL_SERVER_ERROR"
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Handle validation errors clearly
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Invalid request data. Please check your input.",
            "errors": exc.errors()
        }
    )

# ----------------------------
# CORS SETTINGS
# ----------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# MONITORING MIDDLEWARE
# ----------------------------
app.add_middleware(MonitoringMiddleware)


# ----------------------------
# OBSERVABILITY (Phase 7A + 7C)
# ----------------------------
# Order matters: FastAPI wraps middleware in reverse registration order, so
# the *last* registered middleware runs *first* on the inbound path. We want
# the request-id middleware to run first so every downstream log line sees
# the ID, then the Prometheus middleware, then the tenant-context middleware
# (so the Prometheus handler can read the company label).
install_log_filter()


@app.middleware("http")
async def _prometheus_mw(request: Request, call_next):
    return await prometheus_middleware(request, call_next)


@app.middleware("http")
async def _request_id_mw(request: Request, call_next):
    return await request_id_middleware(request, call_next)


@app.get("/metrics", include_in_schema=False)
def _metrics():
    """Prometheus scrape endpoint (plain text exposition format)."""
    return metrics_endpoint()


# ----------------------------
# TENANT CONTEXT MIDDLEWARE (Phase 0.6B)
# ----------------------------
@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    """Extract JWT and attach tenant scope to the request's ContextVar.

    Unauthenticated routes (/health, /api/auth/login, docs) have no token —
    they run with context = None, and the SQLAlchemy event listener silently
    skips auto-filter for them (non-strict mode).
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        try:
            user = decode_access_token(token)
            if user is not None:
                set_tenant_context(
                    company=user.get("company"),
                    is_super_admin=user.get("role") == "super_admin",
                )
        except Exception:
            # Invalid token — let the endpoint's auth dependency raise 401.
            clear_tenant_context()
    try:
        response = await call_next(request)
    finally:
        clear_tenant_context()
    return response

# ----------------------------
# Register Routers
# ----------------------------
app.include_router(auth.router)
app.include_router(rag.router)
app.include_router(admin.router)
app.include_router(health.router, tags=["health"])

# Unified Agent (User-facing chat)
app.include_router(unified_agent_router)

# Individual Agent Routers (Admin endpoints only)
app.include_router(pto_router)
app.include_router(hr_ticket_router)
app.include_router(company_management_router)

# ----------------------------
# Root Endpoint
# ----------------------------
@app.get("/")
def root():
    return {
        "message": "Welcome to FrontShiftAI API",
        "docs": "/docs",
        "health": "/health",
        "chat_endpoint": "/api/chat/message"
    }

# ----------------------------
# Run server directly
# ----------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
        reload_excludes=["wandb", "__pycache__", ".pytest_cache", ".git", "*.db", "*.sqlite"]
    )