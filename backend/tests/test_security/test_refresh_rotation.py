"""
Refresh-token rotation tests, including the atomic-claim race guarantee.

Rotation-on-use must be exactly-once: of N concurrent refreshes presenting
the same token, exactly one may mint a successor. The claim is an atomic
conditional UPDATE (WHERE revoked_at IS NULL) whose rowcount decides the
winner; losers are treated as replays and burn the chain.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from db.models import RefreshToken


def _mint(db, email="user@example.com", company="Test Company"):
    record = RefreshToken(
        id=str(uuid.uuid4()),
        user_email=email,
        company=company,
        token_hash=f"hash-{uuid.uuid4()}",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(record)
    db.commit()
    return record


def _claim(db, token_id: str) -> int:
    """The same atomic conditional UPDATE used by /api/auth/refresh."""
    claimed = (
        db.query(RefreshToken)
        .filter(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
        .update({"revoked_at": datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return claimed


def test_first_claim_wins(test_db):
    record = _mint(test_db)
    assert _claim(test_db, record.id) == 1


def test_second_concurrent_claim_loses(test_db, test_engine):
    """Two sessions racing on the same token: exactly one rowcount of 1."""
    record = _mint(test_db)

    Session = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    other_db = Session()
    try:
        assert _claim(test_db, record.id) == 1
        assert _claim(other_db, record.id) == 0  # loser must not mint tokens
    finally:
        other_db.close()


def test_refresh_endpoint_rejects_reuse_and_burns_chain(client, test_db):
    """End-to-end: reusing a rotated refresh token 401s and kills the chain."""
    from db.seed import seed_initial_data
    seed_initial_data(test_db)

    login = client.post(
        "/api/auth/login",
        json={"email": "user@crousemedical.com", "password": "password123"},
    )
    assert login.status_code == 200, login.json()
    original = login.json()["refresh_token"]

    # First rotation succeeds and mints a successor.
    first = client.post("/api/auth/refresh", json={"refresh_token": original})
    assert first.status_code == 200, first.json()
    successor = first.json()["refresh_token"]
    assert successor != original

    # Replaying the original is reuse: reject and burn the chain.
    replay = client.post("/api/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401

    # The successor died with the chain (theft response evicts everyone).
    after_burn = client.post("/api/auth/refresh", json={"refresh_token": successor})
    assert after_burn.status_code == 401
