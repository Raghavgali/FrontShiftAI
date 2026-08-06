"""
Local development server for voice session management.
This creates LiveKit tokens and room sessions for the frontend.

Run with: python local_session_server.py
"""

import os
import json
import uuid
import time
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from datetime import timedelta
import uvicorn

# Load environment variables
load_dotenv(dotenv_path=Path(__file__).parent / "scripts" / ".env")

# LiveKit credentials. All three come from the environment with no defaults:
# LIVEKIT_URL used to fall back to a university group project's LiveKit Cloud
# instance, which meant a missing variable silently pointed clients at someone
# else's server instead of failing.
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

_missing = [
    name
    for name, value in (
        ("LIVEKIT_URL", LIVEKIT_URL),
        ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
        ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
    )
    if not value
]
if _missing:
    raise ValueError(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        "Set them in voice_pipeline/scripts/.env or the shell environment. "
        "LIVEKIT_URL looks like wss://<your-project>.livekit.cloud"
    )

# Import LiveKit API for token generation
from livekit import api

app = FastAPI(title="FrontShiftAI Voice Session API (Local)")

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SessionRequest(BaseModel):
    user_email: Optional[str] = None
    company: Optional[str] = None
    user_token: Optional[str] = Field(None, description="User's JWT token for backend authentication")


class SessionResponse(BaseModel):
    session_id: str
    room_name: str
    token: str
    livekit_url: str


@app.get("/")
async def root():
    """Root endpoint - service info"""
    return {
        "service": "FrontShiftAI Voice Agent (Local)",
        "status": "running",
        "version": "1.0.0",
        "platform": "local"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint with detailed status"""
    return {
        "status": "healthy",
        "service": "voice-session-api",
        "livekit_configured": bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET),
        "livekit_url": LIVEKIT_URL,
    }


@app.post("/session", response_model=SessionResponse)
async def create_session(request: SessionRequest):
    """
    Create a new voice session with LiveKit.
    Returns connection details for the frontend.
    
    The user_token is embedded in the participant metadata so the voice agent
    can extract it and use it for authenticated backend API calls.
    """
    try:
        # Validate user token is provided
        if not request.user_token:
            raise HTTPException(
                status_code=401,
                detail="User token is required for authenticated voice sessions"
            )
        
        # Generate unique identifiers
        session_id = uuid.uuid4().hex[:16]
        room_name = f"voice-{session_id}"
        
        # User identity for the participant
        user_identity = request.user_email or f"user-{uuid.uuid4().hex[:8]}"
        
        print(f"🎙️ Creating voice session:")
        print(f"   Session ID: {session_id}")
        print(f"   Room: {room_name}")
        print(f"   User: {user_identity}")
        print(f"   Company: {request.company or 'N/A'}")
        print(f"   User Token: {'✓ provided' if request.user_token else '✗ missing'}")

        # Create LiveKit access token
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        token.with_identity(user_identity)
        token.with_name(user_identity)
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
        ))
        
        # Embed user's backend JWT in metadata for voice agent to extract
        # This allows the voice agent to make authenticated calls to the backend
        metadata = {
            "session_id": session_id,
            "user_email": request.user_email,
            "company": request.company,
            "user_token": request.user_token,  # Backend JWT for RAG/PTO/HR calls
        }
        token.with_metadata(json.dumps(metadata))
        
        # Set token expiry (2 hours)
        token.with_ttl(timedelta(hours=2))
        
        jwt_token = token.to_jwt()
        
        print(f"✅ Session created successfully")
        print(f"   LiveKit URL: {LIVEKIT_URL}")
        print(f"   Metadata includes user_token for backend auth")
        
        return SessionResponse(
            session_id=session_id,
            room_name=room_name,
            token=jwt_token,
            livekit_url=LIVEKIT_URL,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error creating session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    """End a voice session (cleanup)."""
    print(f"🔚 Ending session: {session_id}")
    return {"status": "ended", "session_id": session_id}


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 FrontShiftAI Voice Session API (Local Development)")
    print("=" * 60)
    print(f"LiveKit URL: {LIVEKIT_URL}")
    print(f"API Key: {LIVEKIT_API_KEY[:8]}..." if LIVEKIT_API_KEY else "API Key: NOT SET")
    print("")
    print("Endpoints:")
    print("  GET  /         - Service info")
    print("  GET  /health   - Health check")
    print("  POST /session  - Create new voice session (requires user_token)")
    print("")
    print("JWT Passthrough:")
    print("  The user's backend JWT is embedded in LiveKit participant metadata.")
    print("  Voice agent extracts it to make authenticated backend API calls.")
    print("")
    print("Starting server on http://localhost:8001")
    print("=" * 60)
    
    uvicorn.run(app, host="0.0.0.0", port=8001)
