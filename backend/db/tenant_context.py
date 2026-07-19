"""Tenant context + automatic SQLAlchemy query filtering.

Belt-and-braces multi-tenancy: a request-scoped ``ContextVar`` carries the
current user's ``company``. A ``before_compile`` event listener auto-adds
``WHERE company = :current_company`` to any ORM query whose target model
exposes a ``company`` attribute.

This is additive — existing ``.filter(Model.company == ...)`` calls remain
correct. The listener guarantees that a forgotten filter can no longer leak
across tenants.

**Non-strict mode (default)**: when no tenant context is set (e.g.
unauthenticated routes like ``/health`` or ``/api/auth/login``) the listener
silently skips auto-filtering. That way we don't break login (which queries
``User`` by email PK) or health checks.

**Super-admin bypass**: use ``bypass_tenant_filter(reason, actor)`` as a
context manager. Every use produces an audit log entry tagged ``tenant_bypass``.

**Fail-closed**: if the filter cannot be applied to a query it raises instead
of passing the query through unfiltered. A 500 is preferable to a cross-tenant
leak.

**Known limitation**: ``Query.before_compile`` covers the legacy ``db.query()``
API, which is the only ORM query style used in this codebase (verified: no
``session.execute(select(...))`` call sites outside raw health checks). If 2.0
``select()`` statements are ever introduced, this listener must be extended
with a ``Session.do_orm_execute`` hook using ``with_loader_criteria``.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

from sqlalchemy import event
from sqlalchemy.orm import Query

logger = logging.getLogger(__name__)

_current_company: ContextVar[Optional[str]] = ContextVar("current_company", default=None)
_is_super_admin: ContextVar[bool] = ContextVar("is_super_admin", default=False)
_bypass_filter: ContextVar[bool] = ContextVar("bypass_filter", default=False)


def set_tenant_context(company: Optional[str], is_super_admin: bool = False) -> None:
    """Attach the current request's tenant scope. Call from auth middleware."""
    _current_company.set(company)
    _is_super_admin.set(is_super_admin)


def clear_tenant_context() -> None:
    """Reset to the default (no tenant, no super-admin, no bypass)."""
    _current_company.set(None)
    _is_super_admin.set(False)
    _bypass_filter.set(False)


def get_current_company() -> Optional[str]:
    return _current_company.get()


def is_super_admin() -> bool:
    return _is_super_admin.get()


@contextmanager
def bypass_tenant_filter(reason: str, actor: str) -> Iterator[None]:
    """Temporarily disable auto-tenant-filter for genuinely cross-tenant reads.

    Every invocation produces a structured audit log entry. If a breach is
    ever investigated, ``grep tenant_bypass`` shows every cross-tenant access
    point in the code + runtime audit trail.
    """
    logger.warning(
        "TENANT FILTER BYPASS: actor=%s reason=%s",
        actor,
        reason,
        extra={"audit": True, "event": "tenant_bypass", "actor": actor, "reason": reason},
    )
    token = _bypass_filter.set(True)
    try:
        yield
    finally:
        _bypass_filter.reset(token)


@event.listens_for(Query, "before_compile", retval=True)
def _auto_filter_by_company(query: Query) -> Query:
    """Append ``company == current_company`` to every ORM query that can."""
    if _bypass_filter.get():
        return query

    # Super-admin has cross-tenant visibility by design. Explicit
    # bypass_tenant_filter() is still preferred for mutation sites (audit log),
    # but for aggregate reads (monitoring dashboard) this keeps code clean.
    if _is_super_admin.get():
        return query

    company = _current_company.get()
    if company is None:
        # Non-strict: unauthenticated routes (login, health) query without context.
        # We skip auto-filter rather than raising — existing explicit filters are
        # still enforced by the handler code.
        return query

    try:
        for desc in query.column_descriptions:
            model = desc.get("type") or desc.get("entity")
            if model is None:
                continue
            # Only filter if the model actually has a ``company`` column.
            # enable_assertions(False) is required so the filter can still be
            # applied to queries that already carry LIMIT/OFFSET (.first(),
            # .limit()); without it, Query.filter() raises on those and every
            # such query used to slip through UNFILTERED under the old
            # fail-open error handling.
            if hasattr(model, "company"):
                query = query.enable_assertions(False).filter(model.company == company)
    except Exception as exc:
        # FAIL CLOSED: this is a security boundary. If enforcement cannot be
        # applied, refuse to run the query rather than risk returning another
        # tenant's rows. The request fails with a 500, which is the correct
        # trade-off for a tenancy filter.
        logger.error(
            "tenant auto-filter failed, refusing to run unfiltered query: %s",
            exc,
            extra={"audit": True, "event": "tenant_filter_failure"},
        )
        raise RuntimeError("Tenant filter enforcement failed; query aborted") from exc

    return query