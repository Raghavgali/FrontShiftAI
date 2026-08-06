"""Decode a voice-agent JWT to check its claims and signing secret.

Debug helper. Nothing here is committed: supply both values through the
environment so no credential ever lands in git.

    JWT_SECRET_KEY   the backend signing secret (same value the API uses)
    VOICE_AGENT_JWT  the token to inspect

Usage:
    JWT_SECRET_KEY=... VOICE_AGENT_JWT=... python voice_pipeline/scripts/secret_sanity.py

An earlier revision hardcoded both a signing secret and a super-admin token
for a university group project. Both are dead and should be considered
compromised, since they remain in git history.
"""

import os
import sys

import jwt

SECRET = os.environ.get("JWT_SECRET_KEY")
TOKEN = os.environ.get("VOICE_AGENT_JWT")

missing = [
    name
    for name, value in (("JWT_SECRET_KEY", SECRET), ("VOICE_AGENT_JWT", TOKEN))
    if not value
]
if missing:
    sys.exit(f"Missing required environment variable(s): {', '.join(missing)}")

try:
    decoded = jwt.decode(TOKEN, SECRET, algorithms=["HS256"])
except jwt.InvalidTokenError as exc:
    sys.exit(f"Token failed verification: {exc}")

print(decoded)
