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