"""Test Pydantic schemas"""
import pytest
from pydantic import ValidationError
from schemas.auth import LoginRequest, LoginResponse, UserInfo, CreateUserRequest
from schemas.rag import RAGQueryRequest, RAGQueryResponse

def test_login_request_valid():
    """Test valid login request"""
    request = LoginRequest(email="user@example.com", password="password123")
    assert request.email == "user@example.com"
    assert request.password == "password123"

def test_login_request_invalid_email():
    """Test login request with invalid email"""
    with pytest.raises(ValidationError):
        LoginRequest(email="invalid-email", password="password123")

def test_login_response_valid():
    """Test valid login response (includes Phase 0.7 refresh-token fields)"""
    response = LoginResponse(
        access_token="token123",
        refresh_token="refresh456",
        token_type="bearer",
        expires_in=3600,
        email="user@example.com",
        role="user",
        company="Test Company"
    )
    assert response.access_token == "token123"
    assert response.refresh_token == "refresh456"
    assert response.expires_in == 3600
    assert response.role == "user"

def test_rag_query_request_valid():
    """Test valid RAG query request"""
    request = RAGQueryRequest(query="What is the PTO policy?", top_k=5)
    assert request.query == "What is the PTO policy?"
    assert request.top_k == 5

def test_rag_query_request_default_top_k():
    """Test RAG query request with default top_k"""
    request = RAGQueryRequest(query="Test query")
    assert request.top_k == 5  # default value

def test_create_user_request_valid():
    """Test valid create user request"""
    request = CreateUserRequest(
        email="newuser@example.com",
        password="pass123",
        name="New User",
        company="Test Company",
        role="user"
    )
    assert request.email == "newuser@example.com"
    assert request.role == "user"

def test_create_user_request_default_role():
    """Test create user request with default role"""
    request = CreateUserRequest(
        email="newuser@example.com",
        password="pass123",
        name="New User"
    )
    assert request.role == "user"  # default value