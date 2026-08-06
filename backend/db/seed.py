"""
Database seeding functions
"""
import os
from datetime import date

from db.connection import SessionLocal
from db.models import Company, User, UserRole, PTOBalance
from services.auth_service import get_password_hash

# Company the public demo account belongs to. Its handbook is in the corpus,
# so the demo can answer real questions and file a real PTO request.
DEMO_COMPANY = "Crouse Medical Practice"
DEMO_COMPANY_EMAIL_DOMAIN = "crousemedical.com"


def _seed_password(env_var: str, dev_default: str) -> str:
    """Resolve a seed password from the environment.

    Production must supply its own. Outside production we fall back to a
    well-known development value so local runs and the test suite work with
    no setup. Passwords are always stored as bcrypt hashes.
    """
    value = os.getenv(env_var)
    if value:
        return value
    if os.getenv("ENVIRONMENT") == "production":
        raise RuntimeError(
            f"{env_var} must be set when ENVIRONMENT=production. Refusing to "
            "seed a production database with a default password."
        )
    return dev_default


def seed_initial_data(db_session=None):
    """Seed database with initial companies and users"""
    # Resolve credentials before opening a session so a misconfigured
    # production deploy fails loudly instead of being swallowed below.
    super_admin_email = os.getenv("SEED_SUPER_ADMIN_EMAIL", "admin@frontshiftai.com")
    super_admin_password = _seed_password("SEED_SUPER_ADMIN_PASSWORD", "admin123")
    admin_password = _seed_password("SEED_ADMIN_PASSWORD", "admin123")
    user_password = _seed_password("SEED_USER_PASSWORD", "password123")
    demo_password = _seed_password("SEED_DEMO_PASSWORD", "demo1234")
    current_year = date.today().year

    db = db_session if db_session else SessionLocal()

    try:
        # Check if already seeded
        existing_companies = db.query(Company).count()
        if existing_companies > 0:
            print("Database already seeded, skipping...")
            return
        
        print("🌱 Seeding database...")
        
        # Add Companies
        companies_data = [
            {"name": "Crouse Medical Practice", "domain": "Healthcare", "email_domain": "crousemedical.com", "url": "https://crousemed.com/media/1449/cmp-employee-handbook.pdf"},
            {"name": "Healthcare Services Group", "domain": "Healthcare", "email_domain": "healthcareservices.com", "url": "https://www.hcsgcorp.com/wp-content/uploads/2023/01/Employee-Handbook-Healthcare-Services-Group-January-2023.pdf"},
            {"name": "Lunds & Byerlys", "domain": "Retail", "email_domain": "lundsbyerlys.com", "url": "https://corporate.lundsandbyerlys.com/wp-content/uploads/2024/05/EmployeeHandbook_20190926.pdf"},
            {"name": "Holiday Market", "domain": "Retail", "email_domain": "holidaymarket.com", "url": "https://holiday-market.com/schedules/handbook.pdf"},
            {"name": "End Policies Manufacturing", "domain": "Manufacturing", "email_domain": "endpolicies.com", "url": "https://endpolicies.com/wp-content/uploads/2021/08/employee-handbook-2021-updated-1.pdf"},
            {"name": "B&G Foods", "domain": "Manufacturing", "email_domain": "bgfoods.com", "url": "https://bgfood.com/wp-content/uploads/2022/01/BG-Employee-Handbook-2022.pdf"},
            {"name": "Kinyon Construction", "domain": "Construction", "email_domain": "kinyonconstruction.com", "url": "https://www.kinyonconstruction.com/files/132869525.pdf"},
            {"name": "TNT Construction", "domain": "Construction", "email_domain": "tntconstruction.com", "url": "https://www.tntconstructionmn.com/wp-content/uploads/2018/05/TNT-Construction-Inc-Handbook_Final-2018.pdf"},
            {"name": "Alta Peruvian Lodge", "domain": "Hospitality", "email_domain": "altaperuvian.com", "url": "https://www.altaperuvian.com/wp-content/uploads/2017/01/APL-Empl-Manual-Revised-12-22-16-fixed.pdf"},
            {"name": "Western University Hospitality Services", "domain": "Hospitality", "email_domain": "westernhospitality.com", "url": "https://www.hospitalityservices.uwo.ca/staff/handbook.pdf"},
            {"name": "Old National Bank", "domain": "Finance", "email_domain": "oldnational.com", "url": "https://www.oldnational.com/globalassets/onb-site/onb-documents/onb-about-us/onb-team-member-handbook/team-member-handbook.pdf"},
            {"name": "Home Bank", "domain": "Finance", "email_domain": "homebank.com", "url": "https://cdn.firstbranchcms.com/kcms-doc/472/88717/2025-Home-Bank-Employee-Handbook.25.02.24.13.53.40.pdf"},
            {"name": "The Clean Space", "domain": "Cleaning&Maintenance", "email_domain": "cleanspace.com", "url": "https://thecleanspace.com/wp-content/uploads/2024/09/8.-Employee-Handbook-v4.8.pdf"},
            {"name": "AFL Cleaning Services", "domain": "Cleaning&Maintenance", "email_domain": "aflcleaning.com", "url": "https://www.aflcleaningservices.com/resources/AFL_EmployeeHandbook_new.pdf"},
            {"name": "O'Neill Logistics", "domain": "Logistics", "email_domain": "oneilllogistics.com", "url": "https://www.oneilllogistics.com/wp-content/uploads/2023/06/NJ-Warehouse-2022-Employee-Handbook.pdf"},
            {"name": "Buchheit Logistics", "domain": "Logistics", "email_domain": "buchheitlogistics.com", "url": "https://driver.buchheitlogistics.com/wp-content/uploads/2021/06/Logistics-Team-Member-Handbook.pdf"},
            {"name": "Jacob Heating and Cooling", "domain": "FieldServiceTechnicians", "email_domain": "jacobhac.com", "url": "https://www.jacobhac.com/wp-content/uploads/2021/01/Jacob-HAC-Employee-Handbook.pdf"},
            {"name": "CRA Staffing", "domain": "FieldServiceTechnicians", "email_domain": "crastaffing.com", "url": "https://www.crastaffing.com/wp-content/uploads/2020/01/2018-Field-Staff-Handbook-PDF.pdf"},
            {"name": "Prestige Janitorial Service", "domain": "Cleaning&Maintenance", "email_domain": "prestigejanitorial.com", "url": "https://www.phoenixjanitorialservice.net/wp-content/uploads/2017/05/2018-Employee-Handbook.pdf"},
        ]
        
        for company_data in companies_data:
            company = Company(**company_data)
            db.add(company)
        
        # Add Super Admin
        super_admin = User(
            email=super_admin_email,
            password=get_password_hash(super_admin_password),
            name="Super Admin",
            role=UserRole.SUPER_ADMIN,
            company=None
        )
        db.add(super_admin)
        
        # Add Company Admins
        company_admins = [
            {"email": "admin@crousemedical.com", "name": "Crouse Admin", "company": "Crouse Medical Practice"},
            {"email": "admin@healthcareservices.com", "name": "Healthcare Services Admin", "company": "Healthcare Services Group"},
            {"email": "admin@lundsbyerlys.com", "name": "Lunds Admin", "company": "Lunds & Byerlys"},
            {"email": "admin@holidaymarket.com", "name": "Holiday Market Admin", "company": "Holiday Market"},
            {"email": "admin@endpolicies.com", "name": "End Policies Admin", "company": "End Policies Manufacturing"},
            {"email": "admin@bgfoods.com", "name": "B&G Foods Admin", "company": "B&G Foods"},
            {"email": "admin@kinyonconstruction.com", "name": "Kinyon Admin", "company": "Kinyon Construction"},
            {"email": "admin@tntconstruction.com", "name": "TNT Admin", "company": "TNT Construction"},
            {"email": "admin@altaperuvian.com", "name": "Alta Peruvian Admin", "company": "Alta Peruvian Lodge"},
            {"email": "admin@westernhospitality.com", "name": "Western Hospitality Admin", "company": "Western University Hospitality Services"},
            {"email": "admin@oldnational.com", "name": "Old National Admin", "company": "Old National Bank"},
            {"email": "admin@homebank.com", "name": "Home Bank Admin", "company": "Home Bank"},
            {"email": "admin@cleanspace.com", "name": "Clean Space Admin", "company": "The Clean Space"},
            {"email": "admin@aflcleaning.com", "name": "AFL Cleaning Admin", "company": "AFL Cleaning Services"},
            {"email": "admin@oneilllogistics.com", "name": "O'Neill Admin", "company": "O'Neill Logistics"},
            {"email": "admin@buchheitlogistics.com", "name": "Buchheit Admin", "company": "Buchheit Logistics"},
            {"email": "admin@jacobhac.com", "name": "Jacob HAC Admin", "company": "Jacob Heating and Cooling"},
            {"email": "admin@crastaffing.com", "name": "CRA Staffing Admin", "company": "CRA Staffing"},
            {"email": "admin@prestigejanitorial.com", "name": "Prestige Admin", "company": "Prestige Janitorial Service"},
        ]
        
        for admin_data in company_admins:
            admin = User(
                email=admin_data["email"],
                password=get_password_hash(admin_password),
                name=admin_data["name"],
                role=UserRole.COMPANY_ADMIN,
                company=admin_data["company"]
            )
            db.add(admin)
        
        # Add Sample Users
        sample_users = [
            {"email": "user@crousemedical.com", "name": "John Doe", "company": DEMO_COMPANY},
            {"email": "employee@crousemedical.com", "name": "Jane Smith", "company": DEMO_COMPANY},
        ]

        for user_data in sample_users:
            user = User(
                email=user_data["email"],
                password=get_password_hash(user_password),
                name=user_data["name"],
                role=UserRole.USER,
                company=user_data["company"]
            )
            db.add(user)

        # Public demo account. Deliberately a plain USER scoped to one
        # company, so anyone trying the hosted demo sees the tenant isolation
        # working and cannot reach another company's data or the admin views.
        demo_user = User(
            email=f"demo@{DEMO_COMPANY_EMAIL_DOMAIN}",
            password=get_password_hash(demo_password),
            name="Demo Employee",
            role=UserRole.USER,
            company=DEMO_COMPANY
        )
        db.add(demo_user)

        # PTO balances are keyed by calendar year, so seed the current one or
        # the agent finds no balance and the demo looks broken.
        pto_balances = [
            {"email": "user@crousemedical.com", "company": DEMO_COMPANY, "year": current_year, "total_days": 15.0, "used_days": 0.0, "pending_days": 0.0},
            {"email": "employee@crousemedical.com", "company": DEMO_COMPANY, "year": current_year, "total_days": 20.0, "used_days": 2.0, "pending_days": 0.0},
            {"email": f"demo@{DEMO_COMPANY_EMAIL_DOMAIN}", "company": DEMO_COMPANY, "year": current_year, "total_days": 18.0, "used_days": 3.0, "pending_days": 0.0},
        ]

        for balance_data in pto_balances:
            balance = PTOBalance(**balance_data)
            db.add(balance)
        
        db.commit()
        print("✅ Database seeded successfully!")
        
    except Exception as e:
        print(f"❌ Error seeding database: {e}")
        db.rollback()
    finally:
        if not db_session:  # Only close if we created it
            db.close()