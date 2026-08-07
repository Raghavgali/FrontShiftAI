"""Test database seeding"""
import pytest
from db.seed import seed_initial_data
from db.models import User, Company, UserRole

def test_seed_initial_data(test_db):
    """Test that seeding creates expected data"""
    # Mock the session
    import db.seed
    original_session = db.seed.SessionLocal
    db.seed.SessionLocal = lambda: test_db
    
    try:
        seed_initial_data()
        
        # Check companies were created
        companies = test_db.query(Company).all()
        assert len(companies) == 19
        
        # Check super admin was created
        super_admin = test_db.query(User).filter(User.role == UserRole.SUPER_ADMIN).first()
        assert super_admin is not None
        assert super_admin.email == "admin@frontshiftai.com"

        # Check company admins were created
        admins = test_db.query(User).filter(User.role == UserRole.COMPANY_ADMIN).all()
        assert len(admins) == 19

        # Two sample users plus the public demo account
        users = test_db.query(User).filter(User.role == UserRole.USER).all()
        assert len(users) == 3
        assert any(u.email == "demo@crousemedical.com" for u in users)

        # Every seeded password must be a bcrypt hash, never plaintext
        for user in test_db.query(User).all():
            assert user.password.startswith("$2b$"), (
                f"{user.email} was seeded with a non-bcrypt password"
            )

    finally:
        db.seed.SessionLocal = original_session


def test_production_seed_requires_explicit_passwords(test_db, monkeypatch):
    """A fresh production database must not get default passwords."""
    import db.seed
    monkeypatch.setattr(db.seed, "SessionLocal", lambda: test_db)
    monkeypatch.setenv("ENVIRONMENT", "production")
    for var in (
        "SEED_SUPER_ADMIN_PASSWORD",
        "SEED_ADMIN_PASSWORD",
        "SEED_USER_PASSWORD",
        "SEED_DEMO_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(RuntimeError, match="SEED_SUPER_ADMIN_PASSWORD"):
        seed_initial_data()


def test_seeded_production_startup_does_not_need_seed_passwords(test_db, monkeypatch):
    """The already-seeded path must not touch seed credentials.

    seed_initial_data runs on every startup. Resolving the passwords before
    the "already seeded" check made every cold start in production depend on
    env vars that only matter once, which crash-looped the service.
    """
    import db.seed
    monkeypatch.setattr(db.seed, "SessionLocal", lambda: test_db)

    seed_initial_data()  # seed once, with development defaults

    monkeypatch.setenv("ENVIRONMENT", "production")
    for var in (
        "SEED_SUPER_ADMIN_PASSWORD",
        "SEED_ADMIN_PASSWORD",
        "SEED_USER_PASSWORD",
        "SEED_DEMO_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)

    seed_initial_data()  # must be a no-op, not a RuntimeError