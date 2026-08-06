"""
Authentication and user management service
"""
from db import SessionLocal, User, Company, UserRole
from typing import Tuple, List, Dict, Optional
from sqlalchemy.orm import Session
import bcrypt
import logging

logger = logging.getLogger(__name__)

def verify_password(plain_password, hashed_password):
    # bcrypt.checkpw expects bytes
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')
    if isinstance(plain_password, str):
        plain_password = plain_password.encode('utf-8')
    return bcrypt.checkpw(plain_password, hashed_password)

def get_password_hash(password):
    if isinstance(password, str):
        password = password.encode('utf-8')
    # hashpw returns bytes, decode to store as string
    return bcrypt.hashpw(password, bcrypt.gensalt()).decode('utf-8')

def validate_credentials(email: str, password: str, db: Optional[Session] = None) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """
    Validate user credentials
    Returns: (is_valid, company, role)
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return False, None, None, None
        
        # Only bcrypt hashes are accepted. A row that is not hashed is a
        # seeding or migration bug, so it fails closed and gets logged rather
        # than falling back to a plaintext comparison.
        if not str(user.password or "").startswith(("$2a$", "$2b$", "$2y$")):
            logger.error(
                "Refusing login for %s: stored password is not a bcrypt hash. "
                "Reset it with update_user_password().",
                email,
            )
            return False, None, None, None

        if not verify_password(password, user.password):
            return False, None, None, None

        return True, user.company, user.role.value, user.name
    
    finally:
        if close_db:
            db.close()

def get_company_from_email(email: str, db: Optional[Session] = None) -> Optional[str]:
    """Get company name from user email"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        user = db.query(User).filter(User.email == email).first()
        return user.company if user else None
    finally:
        if close_db:
            db.close()

def get_all_companies(db: Optional[Session] = None) -> List[Dict]:
    """Get all companies"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        companies = db.query(Company).all()
        return [
            {
                "name": c.name,
                "domain": c.domain,
                "email_domain": c.email_domain
            }
            for c in companies
        ]
    finally:
        if close_db:
            db.close()

def get_all_company_admins(db: Optional[Session] = None) -> List[Dict]:
    """Get all company admins (for super admin)"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        admins = db.query(User).filter(User.role == UserRole.COMPANY_ADMIN).all()
        return [
            {
                "email": admin.email,
                "name": admin.name,
                "company": admin.company,
                "created_at": admin.created_at.isoformat() if admin.created_at else None
            }
            for admin in admins
        ]
    finally:
        if close_db:
            db.close()

def get_users_by_company(company: str, db: Optional[Session] = None) -> List[Dict]:
    """Get all users in a company"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        users = db.query(User).filter(
            User.company == company,
            User.role == UserRole.USER
        ).all()
        
        return [
            {
                "email": user.email,
                "name": user.name,
                "created_at": user.created_at.isoformat() if user.created_at else None
            }
            for user in users
        ]
    finally:
        if close_db:
            db.close()

def add_user(email: str, password: str, company: Optional[str], name: str, role: str = "user", db: Optional[Session] = None) -> Tuple[bool, str]:
    """Add a new user"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            return False, "User with this email already exists"
        
        # Validate role
        if role not in ["user", "company_admin", "super_admin"]:
            return False, "Invalid role"
        
        # Create user
        new_user = User(
            email=email,
            password=get_password_hash(password),  # Hashed!
            name=name,
            company=company,
            role=UserRole(role)
        )
        
        db.add(new_user)
        db.commit()
        
        return True, f"User {email} created successfully"
    
    except Exception as e:
        db.rollback()
        return False, f"Error creating user: {str(e)}"
    
    finally:
        if close_db:
            db.close()

def delete_user(email: str, db: Optional[Session] = None) -> Tuple[bool, str]:
    """Delete a user"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return False, "User not found"
        
        # Don't allow deleting super admin
        if user.role == UserRole.SUPER_ADMIN:
            return False, "Cannot delete super admin"
        
        db.delete(user)
        db.commit()
        
        return True, f"User {email} deleted successfully"
    
    except Exception as e:
        db.rollback()
        return False, f"Error deleting user: {str(e)}"
    
    finally:
        if close_db:
            db.close()

def update_user_password(email: str, new_password: str, db: Optional[Session] = None) -> Tuple[bool, str]:
    """Update user password"""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    
    try:
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            return False, "User not found"
        
        user.password = get_password_hash(new_password)  # Hashed!
        db.commit()
        
        return True, f"Password updated for {email}"
    
    except Exception as e:
        db.rollback()
        return False, f"Error updating password: {str(e)}"
    
    finally:
        if close_db:
            db.close()