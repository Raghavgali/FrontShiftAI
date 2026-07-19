"""
Concurrency-safety tests for the idempotency reserve/store/release cycle.

The (key, company) primary key makes reserve() a mutual-exclusion point:
of N concurrent requests with the same key, exactly one may perform the
side effect. These tests exercise every interleaving outcome.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from api.idempotency import IdempotencyGuard, PENDING_STATUS
from db.models import IdempotencyRecord

COMPANY = "Test Company"
ENDPOINT = "/api/pto/chat"


@pytest.fixture
def second_db(test_engine):
    """A second, independent session simulating a concurrent request."""
    Session = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


def test_reserve_acquires_and_blocks_in_flight_duplicate(test_db, second_db):
    """Second concurrent request with the same key must get 409, not run."""
    g1 = IdempotencyGuard(key="k-1")
    g2 = IdempotencyGuard(key="k-1")

    assert g1.reserve(test_db, COMPANY, ENDPOINT) is None  # winner proceeds

    with pytest.raises(HTTPException) as exc_info:
        g2.reserve(second_db, COMPANY, ENDPOINT)
    assert exc_info.value.status_code == 409
    assert exc_info.value.headers.get("Retry-After")


def test_reserve_replays_completed_response(test_db, second_db):
    """After the winner stores, a duplicate gets the cached body verbatim."""
    g1 = IdempotencyGuard(key="k-2")
    assert g1.reserve(test_db, COMPANY, ENDPOINT) is None
    g1.store(test_db, COMPANY, ENDPOINT, status_code=200, body={"response": "done", "request_id": "r-1"})

    g2 = IdempotencyGuard(key="k-2")
    cached = g2.reserve(second_db, COMPANY, ENDPOINT)
    assert cached == {"response": "done", "request_id": "r-1"}


def test_store_promotes_reservation_single_row(test_db):
    """store() must UPDATE the pending placeholder, not insert a second row."""
    g = IdempotencyGuard(key="k-3")
    g.reserve(test_db, COMPANY, ENDPOINT)
    g.store(test_db, COMPANY, ENDPOINT, status_code=200, body={"ok": True})

    rows = (
        test_db.query(IdempotencyRecord)
        .filter(IdempotencyRecord.key == "k-3", IdempotencyRecord.company == COMPANY)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status_code == 200
    assert rows[0].status_code != PENDING_STATUS


def test_release_allows_clean_retry(test_db, second_db):
    """A failed request must release so the client's retry gets a real attempt."""
    g1 = IdempotencyGuard(key="k-4")
    assert g1.reserve(test_db, COMPANY, ENDPOINT) is None
    g1.release(test_db, COMPANY)  # simulates the endpoint failure path

    g2 = IdempotencyGuard(key="k-4")
    assert g2.reserve(second_db, COMPANY, ENDPOINT) is None  # retry proceeds


def test_release_never_deletes_completed_records(test_db):
    """release() only removes PENDING placeholders, never real responses."""
    g = IdempotencyGuard(key="k-5")
    g.reserve(test_db, COMPANY, ENDPOINT)
    g.store(test_db, COMPANY, ENDPOINT, status_code=200, body={"ok": True})
    g.release(test_db, COMPANY)

    assert g.reserve(test_db, COMPANY, ENDPOINT) == {"ok": True}


def test_keys_are_tenant_scoped(test_db, second_db):
    """The same key in another tenant is a different reservation entirely."""
    g1 = IdempotencyGuard(key="k-6")
    g2 = IdempotencyGuard(key="k-6")
    assert g1.reserve(test_db, "Company A", ENDPOINT) is None
    assert g2.reserve(second_db, "Company B", ENDPOINT) is None  # no 409


def test_disabled_guard_is_noop(test_db):
    """No Idempotency-Key header: reserve/store/release must all no-op."""
    g = IdempotencyGuard(key=None)
    assert g.reserve(test_db, COMPANY, ENDPOINT) is None
    g.store(test_db, COMPANY, ENDPOINT, status_code=200, body={"ok": True})
    g.release(test_db, COMPANY)
    assert test_db.query(IdempotencyRecord).count() == 0
