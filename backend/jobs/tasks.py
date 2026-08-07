import os
import json
import subprocess
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from .worker import celery_app
from db import SessionLocal
from db.models import Task, Company, IdempotencyRecord
from utils.resilience import get_policy, resilient

logger = logging.getLogger(__name__)

# Paths - using pathlib for project-relative paths
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_PIPELINE_DIR = PROJECT_ROOT / "data_pipeline"
URL_JSON_PATH = DATA_PIPELINE_DIR / "data" / "url.json"
PIPELINE_SCRIPT = DATA_PIPELINE_DIR / "scripts" / "pipeline_runner.py"
DATA_DIR = DATA_PIPELINE_DIR / "data"
GCS_BUCKET = "gs://frontshiftai-data"

# Timeout/retry/backoff for the bucket sync come from the shared policy matrix
# (docs/resilience_policy.md): 300s timeout, 3 retries, exponential 5s base,
# so 5s/10s/20s between attempts.
_GCS_SYNC_POLICY = get_policy("gcs_sync")


class GCSSyncError(RuntimeError):
    """Raised when the data directory cannot be pushed to the bucket."""


@resilient(policy="gcs_sync")
def sync_data_dir_to_gcs(data_dir: Path, bucket: str, env: dict) -> None:
    """Push ``data_dir`` to ``bucket`` with retries and a pre-flight check.

    A single network blip used to fail the whole ingestion task. The retry
    budget comes from the ``gcs_sync`` policy. The pre-flight check refuses to
    sync a missing or empty data directory, so a pipeline that produced nothing
    cannot be mistaken for a successful sync.
    """
    if not data_dir.exists() or not any(data_dir.iterdir()):
        # Not retryable in any useful sense, but raising here still surfaces
        # the real problem instead of silently reporting success.
        raise GCSSyncError(
            f"Refusing to sync {data_dir}: directory is missing or empty."
        )

    try:
        result = subprocess.run(
            ["gsutil", "-m", "rsync", "-r", str(data_dir), bucket],
            capture_output=True,
            text=True,
            timeout=_GCS_SYNC_POLICY.timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        # Re-wrapped so the caller does not confuse this with the pipeline
        # subprocess timing out.
        raise GCSSyncError(
            f"GCS sync exceeded {_GCS_SYNC_POLICY.timeout_s}s."
        ) from exc
    except FileNotFoundError as exc:
        raise GCSSyncError("gsutil is required to sync data to GCS.") from exc

    if result.returncode != 0:
        raise GCSSyncError(f"GCS sync failed: {(result.stderr or '').strip()}")

    logger.info("Synced %s to %s", data_dir, bucket)


@celery_app.task(bind=True)
def process_company_pipeline_task(self, task_id: str, company_name: str, domain: str, url: str):
    """
    Celery task to run the company ingestion pipeline
    """
    db: Session = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    
    if not task:
        logger.error(f"Task {task_id} not found")
        return
    
    try:
        # Update status to running
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.message = "Starting pipeline processing..."
        db.commit()
        
        # Step 1: Update url.json
        task.message = "Updating url.json..."
        db.commit()
        
        # Ensure directory exists
        URL_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        
        companies_data = []
        if URL_JSON_PATH.exists():
            with open(URL_JSON_PATH, 'r') as f:
                try:
                    companies_data = json.load(f)
                except json.JSONDecodeError:
                    companies_data = []
        
        # Add new company if not exists
        if not any(c.get("company") == company_name for c in companies_data):
            new_company = {
                "domain": domain,
                "company": company_name,
                "url": str(url)
            }
            companies_data.append(new_company)
            
            with open(URL_JSON_PATH, 'w') as f:
                json.dump(companies_data, f, indent=4)
        
        # Step 2: Run pipeline
        task.message = "Running data pipeline..."
        db.commit()
        
        env = os.environ.copy()
        # Ensure PYTHONPATH includes project root
        env["PYTHONPATH"] = f"{PROJECT_ROOT}:{env.get('PYTHONPATH', '')}"
        
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
            env=env,
            cwd=str(PROJECT_ROOT) # Run from project root
        )
        
        if result.returncode != 0:
            raise Exception(f"Pipeline failed: {result.stderr}")
        
        # Step 3: Sync to GCS
        task.message = "Syncing to Google Cloud Storage..."
        db.commit()
        
        sync_data_dir_to_gcs(DATA_DIR, GCS_BUCKET, env)

        # Success
        task.status = "completed"
        task.message = "Company added successfully!"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        
    except subprocess.TimeoutExpired:
        task.status = "failed"
        task.error = "Pipeline execution timeout"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        
    except Exception as e:
        logger.exception(f"Task {task_id} failed")
        task.status = "failed"
        task.error = str(e)
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        
    finally:
        db.close()

@celery_app.task
def purge_stale_idempotency_records():
    """Delete IdempotencyRecord rows older than 24h.

    Scheduled daily (see celery beat config). Records exist only to dedupe
    retries within a short window — beyond 24h they're dead weight.
    """
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        deleted = (
            db.query(IdempotencyRecord)
            .filter(IdempotencyRecord.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        logger.info("purge_stale_idempotency_records: deleted %d rows", deleted)
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        logger.exception("purge_stale_idempotency_records failed")
        raise
    finally:
        db.close()


CHECKPOINT_RETENTION_DAYS = int(os.getenv("CHECKPOINT_RETENTION_DAYS", "30"))


@celery_app.task
def cleanup_stale_checkpoints(retention_days: int = None):
    """Phase 5.5E: drop durable LangGraph state for dormant conversations.

    A checkpoint exists to let a conversation resume. Once a conversation has
    been dormant for the retention window nobody is going to resume it, and the
    rows are just a growing pile of serialised graph state in Neon.

    Two passes, because not every thread has a conversation to age out:

    1. Conversations whose ``updated_at`` is older than the cutoff. This is the
       rule the plan asks for. Thread ids are enumerated exactly per
       conversation, never matched with ``LIKE``, so a conversation id
       containing a wildcard character cannot widen the delete.
    2. Any remaining thread whose newest checkpoint predates the cutoff. This
       catches the threads pass 1 cannot see: the disposable threads minted by
       the standalone ``/api/pto/chat`` and ``/api/hr-ticket/chat`` routes, and
       orphans left by a conversation deleted before this task existed.

    Cross-tenant by nature, so the Conversation scan runs under
    ``bypass_tenant_filter``: it must see every tenant's dormant conversations,
    and the bypass is what puts that on the audit trail.

    **Not running in production yet.** Redis is not provisioned, so neither the
    Celery worker nor beat is deployed. Until they are, this has to be invoked
    by hand, and checkpoint rows accumulate. That is bounded by conversation
    volume rather than unbounded, but it is a real gap.
    """
    from agents.utils.checkpointer import (
        purge_checkpoints_for_conversations,
        purge_checkpoints_older_than,
    )
    from db.models import Conversation
    from db.tenant_context import bypass_tenant_filter

    days = CHECKPOINT_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    db: Session = SessionLocal()
    try:
        with bypass_tenant_filter(
            reason=f"scheduled checkpoint cleanup, retention={days}d",
            actor="jobs.tasks.cleanup_stale_checkpoints",
        ):
            stale_ids = [
                row[0]
                for row in db.query(Conversation.id)
                .filter(Conversation.updated_at < cutoff)
                .all()
            ]
    finally:
        db.close()

    by_conversation = purge_checkpoints_for_conversations(stale_ids)
    orphaned = purge_checkpoints_older_than(cutoff)

    logger.info(
        "cleanup_stale_checkpoints: retention=%dd conversations=%d "
        "checkpoints_by_conversation=%d checkpoints_orphaned=%d",
        days, len(stale_ids), by_conversation, orphaned,
    )
    return {
        "retention_days": days,
        "stale_conversations": len(stale_ids),
        "deleted_by_conversation": by_conversation,
        "deleted_orphaned": orphaned,
    }


@celery_app.task(bind=True)
def process_delete_company_task(self, task_id: str, company_name: str):
    """
    Celery task to handle company deletion and RAG rebuild
    """
    db: Session = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    
    if not task:
        logger.error(f"Task {task_id} not found")
        return
    
    try:
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.message = "Updating url.json..."
        db.commit()
        
        # Step 1: Remove from url.json
        if URL_JSON_PATH.exists():
            with open(URL_JSON_PATH, 'r') as f:
                try:
                    companies_data = json.load(f)
                except json.JSONDecodeError:
                    companies_data = []
            
            # Filter out deleted company
            companies_data = [c for c in companies_data if c.get("company") != company_name]
            
            with open(URL_JSON_PATH, 'w') as f:
                json.dump(companies_data, f, indent=4)
                
        # Step 2: Rebuild Pipeline (same as add)
        task.message = "Rebuilding RAG index (this may take a while)..."
        db.commit()
        
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{PROJECT_ROOT}:{env.get('PYTHONPATH', '')}"
        
        result = subprocess.run(
            ["python", str(PIPELINE_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            cwd=str(PROJECT_ROOT)
        )
        
        if result.returncode != 0:
            raise Exception(f"Pipeline rebuild failed: {result.stderr}")
            
        # Step 3: Sync to GCS
        task.message = "Syncing changes to Google Cloud Storage..."
        db.commit()
        
        sync_data_dir_to_gcs(DATA_DIR, GCS_BUCKET, env)
    
        task.status = "completed"
        task.message = "Company deleted and index rebuilt successfully!"
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
        
    except Exception as e:
        logger.exception(f"Deletion task {task_id} failed")
        task.status = "failed"
        task.error = str(e)
        task.completed_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
