"""
Pytest fixtures for backend tests
"""
import pytest
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
# Add backend directory to path
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))
# Add project root to path
sys.path.insert(0, str(backend_path.parent))

from db.connection import Base, get_db

# Test database URL (use in-memory SQLite for tests)
TEST_DATABASE_URL = "sqlite://"


@pytest.fixture(scope="function")
def test_engine():
    """Create a test database engine"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # Important for in-memory SQLite
    )
    
    # Import models to register them
    from db import models
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    yield engine
    
    # Drop all tables after test
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

@pytest.fixture(scope="function")
def test_db(test_engine):
    """Create a fresh test database session for each test"""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def client(test_engine):
    """Create a test client with test database"""
    # Imported lazily so DB-only test modules don't pay for (or break on)
    # the full app import chain (chat_pipeline, torch, etc.).
    from main import app

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_db] = override_get_db
    
    with TestClient(app) as test_client:
        yield test_client
    
    app.dependency_overrides.clear()

@pytest.fixture
def sample_user_data():
    """Sample user data for tests"""
    return {
        "email": "test@example.com",
        "password": "testpass123",
        "name": "Test User",
        "company": "Test Company",
        "role": "user"
    }

@pytest.fixture
def sample_company_data():
    """Sample company data for tests"""
    return {
        "name": "Test Company",
        "domain": "Technology",
        "email_domain": "testcompany.com",
        "url": "https://testcompany.com/handbook.pdf"
    }

@pytest.fixture
def auth_headers(client, test_db):
    """Get authentication headers for a test user"""
    # Seed the database directly with test_db
    from db.seed import seed_initial_data
    seed_initial_data(test_db)
    
    # Login
    response = client.post(
        "/api/auth/login",
        json={"email": "user@crousemedical.com", "password": "password123"}
    )
    
    assert response.status_code == 200, f"Login failed: {response.json()}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def admin_headers(client, test_db):
    """Get authentication headers for an admin user"""
    # Seed the database directly with test_db
    from db.seed import seed_initial_data
    seed_initial_data(test_db)
    
    # Login as company admin
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@crousemedical.com", "password": "admin123"}
    )
    
    assert response.status_code == 200, f"Login failed: {response.json()}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def super_admin_headers(client, test_db):
    """Get authentication headers for super admin"""
    # Seed the database directly with test_db
    from db.seed import seed_initial_data
    seed_initial_data(test_db)
    
    # Login as super admin
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@group9.com", "password": "admin123"}
    )
    
    assert response.status_code == 200, f"Login failed: {response.json()}"
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}