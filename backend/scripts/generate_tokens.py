import sys
import os
from datetime import datetime, timedelta, timezone
from jose import jwt

# Add current directory to path so we can import backend modules (if needed)
# But here we will implement a standalone generator to avoid DB dependencies
sys.path.append(os.getcwd())

# Configuration - replicating auth.py logic
# We will read JWT_SECRET_KEY from env if available, otherwise prompt or error
# Since I cannot read .env directly due to permission, I'll try to load it using python-dotenv
try:
    from dotenv import load_dotenv
    # Script is now in backend/scripts/, so .env is at ../.env
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 365  # 1 year

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    if not SECRET_KEY:
        print("ERROR: JWT_SECRET_KEY not found in environment")
        return None
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Define users to generate tokens for
users = [
    {
        "sub": "admin@frontshiftai.com",
        "company": "FrontShiftAI",
        "role": "super_admin",
        "label": "SUPER ADMIN"
    },
    {
        "sub": "admin@crousemedical.com",
        "company": "Crouse Medical Practice", 
        "role": "company_admin",
        "label": "COMPANY ADMIN"
    },
    {
        "sub": "user@crousemedical.com",
        "company": "Crouse Medical Practice",
        "role": "user",
        "label": "REGULAR USER"
    }
]

# File to save tokens to
output_file = os.path.join(os.path.dirname(__file__), '..', 'tokens.env')

print("-" * 50)
print(f"GENERATING 1-YEAR TOKENS -> {output_file}")
print("-" * 50)

with open(output_file, 'w') as f:
    f.write(f"# Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"# These tokens are valid for 1 year (until {(datetime.now() + timedelta(days=365)).strftime('%Y-%m-%d')})\n\n")
    
    for user in users:
        token_data = {
            "sub": user["sub"],
            "company": user["company"],
            "role": user["role"]
        }
        token = create_access_token(token_data)
        if token:
            token_var = f"{user['label'].replace(' ', '_')}_TOKEN"
            f.write(f"{token_var}={token}\n")
            print(f"Saved {token_var}")

print("-" * 50)
print(f"Tokens saved to {output_file}")
