"""Test authentication service"""
import pytest
from services.auth_service import (
    validate_credentials,
    get_company_from_email,
    get_password_hash,
    add_user,
    delete_user,
    update_user_password
)
from db.models import User, UserRole

def test_validate_credentials_success(test_db):
    """Test successful credential validation"""
    # Create a test user
    user = User(
        email="test@example.com",
        password=get_password_hash("testpass"),
        name="Test User",
        role=UserRole.USER,
        company="Test Company"
    )
    test_db.add(user)
    test_db.commit()
    
    # Mock SessionLocal
    import services.auth_service
    original_session = services.auth_service.SessionLocal
    services.auth_service.SessionLocal = lambda: test_db
    
    try:
        is_valid, company, role, user_name = validate_credentials("test@example.com", "testpass")
        assert is_valid is True
        assert company == "Test Company"
        assert role == "user"
        assert user_name == "Test User"
    finally:
        services.auth_service.SessionLocal = original_session

def test_validate_credentials_wrong_password(test_db):
    """Test credential validation with wrong password"""
    user = User(
        email="test@example.com",
        password=get_password_hash("testpass"),
        name="Test User",
        role=UserRole.USER,
        company="Test Company"
    )
    test_db.add(user)
    test_db.commit()
    
    import services.auth_service
    original_session = services.auth_service.SessionLocal
    services.auth_service.SessionLocal = lambda: test_db
    
    try:
        is_valid, company, role, user_name = validate_credentials("test@example.com", "wrongpass")
        assert is_valid is False
        assert company is None
        assert role is None
        assert user_name is None
    finally:
        services.auth_service.SessionLocal = original_session

def test_add_user_success(test_db):
    """Test adding a new user"""
    import services.auth_service
    original_session = services.auth_service.SessionLocal
    services.auth_service.SessionLocal = lambda: test_db
    
    try:
        success, message = add_user(
            email="newuser@example.com",
            password="pass123",
            company="Test Company",
            name="New User",
            role="user"
        )
        
        assert success is True
        assert "created successfully" in message
        
        # Verify user was created
        user = test_db.query(User).filter(User.email == "newuser@example.com").first()
        assert user is not None
        assert user.name == "New User"
    finally:
        services.auth_service.SessionLocal = original_session

def test_add_duplicate_user(test_db):
    """Test adding a user that already exists"""
    user = User(
        email="existing@example.com",
        password="pass",
        name="Existing",
        role=UserRole.USER,
        company="Test"
    )
    test_db.add(user)
    test_db.commit()
    
    import services.auth_service
    original_session = services.auth_service.SessionLocal
    services.auth_service.SessionLocal = lambda: test_db
    
    try:
        success, message = add_user(
            email="existing@example.com",
            password="pass",
            company="Test",
            name="Duplicate",
            role="user"
        )
        
        assert success is False
        assert "already exists" in message
    finally:
        services.auth_service.SessionLocal = original_session