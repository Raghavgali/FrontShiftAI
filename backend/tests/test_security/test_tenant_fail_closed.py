"""
Tenant auto-filter behavior tests, including the fail-closed guarantee.

The before_compile listener is a security boundary: when it cannot apply
the tenant filter it must abort the query, never pass it through unfiltered.
"""
import pytest
from sqlalchemy.orm import Query

import db.tenant_context as tc
from db.models import IdempotencyRecord


@pytest.fixture(autouse=True)
def clean_tenant_context():
    """Every test starts and ends with no tenant context."""
    tc.clear_tenant_context()
    yield
    tc.clear_tenant_context()


def _seed_two_tenants(db):
    for company in ("Company A", "Company B"):
        db.add(
            IdempotencyRecord(
                key=f"key-{company}",
                company=company,
                endpoint="/x",
                status_code=200,
                response_body="{}",
            )
        )
    db.commit()


def test_auto_filter_scopes_query_to_current_tenant(test_db):
    _seed_two_tenants(test_db)
    tc.set_tenant_context(company="Company A")

    rows = test_db.query(IdempotencyRecord).all()
    assert [r.company for r in rows] == ["Company A"]


def test_auto_filter_applies_to_first_and_limit_queries(test_db):
    """Regression: .first()/.limit() carry LIMIT before compile, which used to
    make Query.filter() raise inside the listener; under the old fail-open
    handling those queries ran UNFILTERED across tenants."""
    _seed_two_tenants(test_db)
    tc.set_tenant_context(company="Company B")

    row = test_db.query(IdempotencyRecord).first()
    assert row is not None and row.company == "Company B"

    rows = test_db.query(IdempotencyRecord).limit(10).all()
    assert [r.company for r in rows] == ["Company B"]


def test_no_context_skips_filter_for_unauthenticated_routes(test_db):
    _seed_two_tenants(test_db)
    # No context (login/health): non-strict mode returns everything; handlers
    # remain responsible for explicit filters.
    assert test_db.query(IdempotencyRecord).count() == 2


def test_super_admin_sees_across_tenants(test_db):
    _seed_two_tenants(test_db)
    tc.set_tenant_context(company=None, is_super_admin=True)
    assert test_db.query(IdempotencyRecord).count() == 2


def test_bypass_is_scoped_and_restores(test_db):
    _seed_two_tenants(test_db)
    tc.set_tenant_context(company="Company A")
    with tc.bypass_tenant_filter(reason="unit test", actor="pytest"):
        assert test_db.query(IdempotencyRecord).count() == 2
    assert test_db.query(IdempotencyRecord).count() == 1


def test_enforcement_failure_fails_closed(test_db, monkeypatch):
    """If the filter cannot be applied, the query must abort, not leak."""
    _seed_two_tenants(test_db)
    tc.set_tenant_context(company="Company A")

    def explode(self):
        raise ValueError("simulated enforcement failure")

    monkeypatch.setattr(Query, "column_descriptions", property(explode))

    with pytest.raises(Exception) as exc_info:
        test_db.query(IdempotencyRecord).all()
    assert "Tenant filter enforcement failed" in str(exc_info.value)
