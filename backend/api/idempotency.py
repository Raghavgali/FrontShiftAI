"""Idempotency-Key support for mutation endpoints.

Usage pattern inside a handler (reserve-then-execute, concurrency-safe)::

    @router.post("/api/pto/chat")
    async def chat(request, current_user, db, idem = Depends(idempotency_guard)):
        cached = idem.reserve(db, current_user["company"], endpoint="/api/pto/chat")
        if cached is not None:
            return cached          # key already completed: replay the body
        try:
            result = do_work(...)  # this caller holds the reservation
            idem.store(db, current_user["company"], endpoint="/api/pto/chat",
                       status_code=200, body=result.model_dump())
            return result
        except Exception:
            idem.release(db, current_user["company"])  # let the client retry
            raise

reserve() INSERTs a PENDING placeholder under the (key, company) primary
key, so of N concurrent requests with the same key exactly one performs the
side effect; the rest get the cached body or a 409 + Retry-After while the
winner is still working.

When the caller (e.g. the voice agent) passes an ``Idempotency-Key`` header,
the first request is processed normally and its response body persisted. Any
subsequent retry with the same key returns the cached body verbatim — no
duplicate PTO rows, no duplicate HR tickets.

Scope is per-tenant: a stray key reuse across tenants cannot leak.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import IdempotencyRecord

logger = logging.getLogger(__name__)

# Sentinel status for a reservation whose work is still in flight. Uses HTTP
# 102 (Processing) so it can never collide with a real cached status code.
PENDING_STATUS = 102


class IdempotencyGuard:
    """Holds the header value (or None) and encapsulates lookup/store.

    Created per-request via the :func:`idempotency_guard` dependency. If the
    client didn't send a key, lookups are no-ops (``None``) and stores silently
    skip — idempotency is opt-in on mutation endpoints.
    """

    def __init__(self, key: Optional[str]):
        self.key = key

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def lookup(self, db: Session, company: str, endpoint: str) -> Optional[Dict[str, Any]]:
        """Return the cached response body if this key was already processed.

        Scoped per tenant: a record for another tenant's same key is invisible.
        Returns ``None`` on miss, on absent header, or on decode failure.
        """
        if not self.enabled or not company:
            return None
        try:
            record = (
                db.query(IdempotencyRecord)
                .filter(
                    IdempotencyRecord.key == self.key,
                    IdempotencyRecord.company == company,
                )
                .first()
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("idempotency lookup failed: %s", exc)
            return None
        if record is None:
            return None
        if record.status_code == PENDING_STATUS:
            # A concurrent holder is still working; treat as a miss here.
            # Callers wanting exactly-once semantics should use reserve().
            return None
        try:
            return json.loads(record.response_body)
        except (TypeError, ValueError):
            logger.error("idempotency record %s has non-JSON body; ignoring", self.key)
            return None

    def reserve(self, db: Session, company: str, endpoint: str) -> Optional[Dict[str, Any]]:
        """Atomically claim this key BEFORE performing the mutation.

        Concurrency-safe exactly-once guard: the (key, company) primary key
        makes the INSERT a mutual-exclusion point, so of N concurrent
        requests with the same key exactly one wins the reservation and
        performs the side effect. Losers either get the cached body (work
        already finished) or a 409 telling them to retry shortly (work still
        in flight).

        Returns None when the caller holds the reservation and must proceed,
        then either store() (success) or release() (failure). Returns the
        cached response body when the key already completed. Raises 409 when
        another request holds the reservation right now.
        """
        if not self.enabled or not company:
            return None

        reservation = IdempotencyRecord(
            key=self.key,
            company=company,
            endpoint=endpoint,
            status_code=PENDING_STATUS,
            response_body="",
        )
        try:
            db.add(reservation)
            db.commit()
            return None  # reservation acquired — caller proceeds
        except IntegrityError:
            db.rollback()
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            logger.warning("idempotency reserve failed open for %s: %s", self.key, exc)
            return None

        existing = (
            db.query(IdempotencyRecord)
            .filter(
                IdempotencyRecord.key == self.key,
                IdempotencyRecord.company == company,
            )
            .first()
        )
        if existing is None:  # pragma: no cover - race with cleanup cron
            return None
        if existing.status_code == PENDING_STATUS:
            raise HTTPException(
                status_code=409,
                detail="A request with this Idempotency-Key is already being processed",
                headers={"Retry-After": "2"},
            )
        try:
            return json.loads(existing.response_body)
        except (TypeError, ValueError):
            logger.error("idempotency record %s has non-JSON body; ignoring", self.key)
            return None

    def release(self, db: Session, company: str) -> None:
        """Drop an unfinished reservation so the client can retry cleanly.

        Call on the failure path after a successful reserve(). Only removes
        the PENDING placeholder; completed records are never deleted here.
        """
        if not self.enabled or not company:
            return
        try:
            db.query(IdempotencyRecord).filter(
                IdempotencyRecord.key == self.key,
                IdempotencyRecord.company == company,
                IdempotencyRecord.status_code == PENDING_STATUS,
            ).delete(synchronize_session=False)
            db.commit()
        except Exception as exc:  # pragma: no cover - defensive
            db.rollback()
            logger.warning("idempotency release failed for %s: %s", self.key, exc)

    def store(
        self,
        db: Session,
        company: str,
        endpoint: str,
        status_code: int,
        body: Any,
    ) -> None:
        """Persist the response for this key.

        Completes a reserve() by promoting the PENDING placeholder to the
        real response body. Falls back to a plain INSERT for callers that
        never reserved (legacy lookup/store pattern). Safe to call even when
        idempotency is not enabled — it just returns.
        """
        if not self.enabled or not company:
            return
        try:
            serialized = json.dumps(body, default=str)
        except (TypeError, ValueError) as exc:
            logger.error("idempotency store: body not JSON-serializable: %s", exc)
            return

        try:
            updated = (
                db.query(IdempotencyRecord)
                .filter(
                    IdempotencyRecord.key == self.key,
                    IdempotencyRecord.company == company,
                )
                .update(
                    {"status_code": status_code, "response_body": serialized},
                    synchronize_session=False,
                )
            )
            if not updated:
                db.add(
                    IdempotencyRecord(
                        key=self.key,
                        company=company,
                        endpoint=endpoint,
                        status_code=status_code,
                        response_body=serialized,
                    )
                )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("idempotency store race or failure for %s: %s", self.key, exc)


def idempotency_guard(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> IdempotencyGuard:
    """FastAPI dependency: inject an :class:`IdempotencyGuard` for the request."""
    return IdempotencyGuard(key=idempotency_key)