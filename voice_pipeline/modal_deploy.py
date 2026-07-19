"""
Modal Deployment for FrontShiftAI Voice Agent
Deploys the LiveKit voice agent on Modal's CPU infrastructure

On-Demand Architecture:
1. Frontend clicks voice button -> calls /session endpoint
2. /session creates LiveKit room + spawns voice worker for that specific room
3. Worker joins room and handles the conversation
4. Worker terminates when session ends (or timeout)

This eliminates the need to manually run the worker before demos.
"""
import modal
import os
import json
from pathlib import Path

# Define the Modal app
app = modal.App("frontshiftai-voice-agent")

# Define the container image with all dependencies
voice_agent_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "livekit>=1.0.0",
        "livekit-agents[openai,silero,assemblyai,turn-detector,deepgram,google,anthropic,cartesia,elevenlabs]>=1.2",
        "livekit-plugins-noise-cancellation",
        "python-dotenv",
        "certifi>=2024.2.2",
        "av",
        "fastapi>=0.110.0",
        "uvicorn>=0.30.0",
        "httpx>=0.24.0",
        "wandb>=0.15.0",
        "prometheus_client>=0.20.0",
        "pyyaml>=6.0",
    )
    .add_local_dir(
        local_path=str(Path(__file__).parent),
        remote_path="/root/voice_pipeline",
        copy=True  # Include in built image
    )
)

# Define Modal secrets required
SECRETS = [
    modal.Secret.from_name("livekit-credentials"),
    modal.Secret.from_name("voice-agent-providers"),
    modal.Secret.from_name("voice-agent-backend"),
]

# Add wandb secret if available (optional)
try:
    SECRETS.append(modal.Secret.from_name("wandb-credentials"))
except Exception:
    pass


# ============================================================================
# Voice Worker - Spawned on-demand for each session
# ============================================================================

@app.function(
    image=voice_agent_image,
    secrets=SECRETS,
    cpu=2.0,
    memory=4096,
    timeout=3600,  # 1 hour max per session
)
def voice_worker_for_room(
    room_name: str,
    session_id: str,
    max_restarts: int = 2,
    heartbeat_timeout: float = 60.0,
) -> None:
    """
    Voice agent worker that handles a specific room.
    
    Uses LiveKit's CLI runner but configured for a single room.
    Spawned on-demand when a user creates a voice session.
    """
    import sys

    sys.path.insert(0, "/root/voice_pipeline")
    from utils.process_supervisor import run_supervised_process

    worker_env = os.environ.copy()
    worker_env["VOICE_PIPELINE_LOG_LEVEL"] = os.getenv(
        "VOICE_PIPELINE_LOG_LEVEL", "INFO"
    )
    worker_env["VOICE_PIPELINE_LOG_TO_FILE"] = "0"
    worker_env["VOICE_SESSION_ID"] = session_id
    worker_env["PYTHONUNBUFFERED"] = "1"

    # ``connect`` is LiveKit's single-session mode. Unlike ``start``, it
    # connects this on-demand Modal function directly to the requested room
    # and exits when that room job ends.
    cmd = [
        sys.executable,
        "-u",
        "/root/voice_pipeline/scripts/main.py",
        "connect",
        "--room",
        room_name,
    ]

    run_supervised_process(
        cmd,
        cwd="/root/voice_pipeline/scripts",
        env=worker_env,
        max_restarts=max_restarts,
        heartbeat_timeout=heartbeat_timeout,
    )


# ============================================================================
# Web API - Creates sessions and spawns workers
# ============================================================================

@app.function(
    image=voice_agent_image,
    secrets=SECRETS,
    cpu=1.0,
    memory=2048,
    timeout=60,
    allow_concurrent_inputs=100,
)
@modal.asgi_app()
def web_api():
    """
    FastAPI web server for creating LiveKit sessions.
    
    When a session is created, it automatically spawns a voice worker
    to handle that room - no manual worker startup needed!
    """
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    from typing import Optional
    from datetime import datetime, timedelta
    from livekit import api
    import uuid
    import httpx

    web_app = FastAPI(title="FrontShiftAI Voice Agent API")

    # CORS configuration - Allow all origins for the voice API
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # Must be False when allow_origins=["*"]
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=3600,
    )

    # Schemas
    class VoiceSessionRequest(BaseModel):
        room_name: Optional[str] = Field(None, description="Custom room name")
        user_email: Optional[str] = Field(None, description="User email")
        company: Optional[str] = Field(None, description="Company name")
        user_token: Optional[str] = Field(None, description="User's JWT token for backend auth")
        metadata: Optional[dict] = Field(None, description="Optional metadata")

    class VoiceSessionResponse(BaseModel):
        session_id: str
        room_name: str
        token: str
        livekit_url: str
        created_at: datetime
        expires_at: datetime
        worker_status: str

    class HealthResponse(BaseModel):
        status: str
        service: str
        version: str
        livekit_configured: bool
        backend_configured: bool
        providers_configured: dict

    @web_app.get("/")
    def root():
        return {
            "service": "FrontShiftAI Voice Agent",
            "status": "running",
            "version": "1.0.0",
            "platform": "Modal",
            "mode": "on-demand workers"
        }

    @web_app.get("/health", response_model=HealthResponse)
    def health():
        """Health check with provider status"""
        livekit_url = os.getenv("LIVEKIT_URL")
        livekit_key = os.getenv("LIVEKIT_API_KEY")
        backend_url = os.getenv("VOICE_AGENT_BACKEND_URL")
        
        providers = {
            "openai": bool(os.getenv("OPENAI_API_KEY")),
            "deepgram": bool(os.getenv("DEEPGRAM_API_KEY")),
            "cartesia": bool(os.getenv("CARTESIA_API_KEY")),
            "assemblyai": bool(os.getenv("ASSEMBLYAI_API_KEY")),
            "wandb": bool(os.getenv("WANDB_API_KEY")),
        }

        return HealthResponse(
            status="healthy",
            service="frontshiftai-voice-agent",
            version="1.0.0",
            livekit_configured=bool(livekit_url and livekit_key),
            backend_configured=bool(backend_url),
            providers_configured=providers,
        )

    @web_app.get("/health/deep")
    async def deep_health_check():
        """Deep health check - tests external connectivity"""
        results = {"status": "checking", "checks": {}}
        
        livekit_url = os.getenv("LIVEKIT_URL")
        livekit_key = os.getenv("LIVEKIT_API_KEY")
        livekit_secret = os.getenv("LIVEKIT_API_SECRET")
        
        if livekit_url and livekit_key and livekit_secret:
            try:
                token = api.AccessToken(livekit_key, livekit_secret)
                token.with_identity("health-check")
                token.with_grants(api.VideoGrants(room_join=True, room="health-check"))
                token.to_jwt()
                results["checks"]["livekit"] = {"status": "ok", "url": livekit_url}
            except Exception as e:
                results["checks"]["livekit"] = {"status": "error", "error": str(e)}
        else:
            results["checks"]["livekit"] = {"status": "not_configured"}
        
        backend_url = os.getenv("VOICE_AGENT_BACKEND_URL")
        if backend_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{backend_url}/health")
                    if resp.status_code == 200:
                        results["checks"]["backend"] = {"status": "ok", "url": backend_url}
                    else:
                        results["checks"]["backend"] = {"status": "error", "code": resp.status_code}
            except Exception as e:
                results["checks"]["backend"] = {"status": "error", "error": str(e)}
        else:
            results["checks"]["backend"] = {"status": "not_configured"}
        
        all_ok = all(c.get("status") == "ok" for c in results["checks"].values())
        results["status"] = "healthy" if all_ok else "degraded"
        
        return results

    @web_app.options("/session")
    async def session_options():
        """Handle preflight OPTIONS request for /session"""
        return JSONResponse(
            content={"message": "OK"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            }
        )

    @web_app.post("/session", response_model=VoiceSessionResponse)
    async def create_session(request: VoiceSessionRequest):
        """
        Create a new voice session and automatically spawn a worker.
        
        Flow:
        1. Validate user token
        2. Create LiveKit room token with user JWT in metadata
        3. Spawn voice worker to handle the room
        4. Return connection details to frontend
        """
        try:
            if not request.user_token:
                raise HTTPException(
                    status_code=401,
                    detail="User token is required for authenticated voice sessions"
                )
            
            livekit_url = os.getenv("LIVEKIT_URL")
            livekit_api_key = os.getenv("LIVEKIT_API_KEY")
            livekit_api_secret = os.getenv("LIVEKIT_API_SECRET")

            if not all([livekit_url, livekit_api_key, livekit_api_secret]):
                raise HTTPException(
                    status_code=500,
                    detail="LiveKit credentials not configured"
                )

            session_id = uuid.uuid4().hex
            room_name = request.room_name or f"voice-{session_id[:16]}"
            user_identity = request.user_email or f"user-{session_id[:8]}"

            print(f"🎙️ Creating voice session:")
            print(f"   Session ID: {session_id}")
            print(f"   Room: {room_name}")
            print(f"   User: {user_identity}")

            # Create user's LiveKit token
            token = api.AccessToken(livekit_api_key, livekit_api_secret)
            token.with_identity(user_identity)
            token.with_name(request.user_email or "Guest User")
            token.with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )

            # Embed user's JWT in metadata
            metadata = {
                "session_id": session_id,
                "user_email": request.user_email,
                "company": request.company,
                "user_token": request.user_token,
                **(request.metadata or {})
            }
            token.with_metadata(json.dumps(metadata))

            expires_at = datetime.utcnow() + timedelta(hours=2)
            token.with_ttl(timedelta(hours=2))

            jwt_token = token.to_jwt()

            # ============================================================
            # SPAWN VOICE WORKER ON-DEMAND
            # This is the key part - worker starts automatically!
            # ============================================================
            print(f"🚀 Spawning voice worker for room: {room_name}")
            voice_worker_for_room.spawn(room_name=room_name, session_id=session_id)
            print(f"✅ Worker spawn initiated")

            return VoiceSessionResponse(
                session_id=session_id,
                room_name=room_name,
                token=jwt_token,
                livekit_url=livekit_url,
                created_at=datetime.utcnow(),
                expires_at=expires_at,
                worker_status="spawning",
            )

        except HTTPException:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create voice session: {str(e)}"
            )

    return web_app


# ============================================================================
# Manual Worker (Backup) - Listens for all rooms
# ============================================================================

@app.function(
    image=voice_agent_image,
    secrets=SECRETS,
    cpu=2.0,
    memory=4096,
    timeout=3600,
)
def start_worker_manual():
    """
    Manual worker start (backup option).
    
    This runs the standard LiveKit agent worker that listens for ALL rooms.
    Use this if on-demand spawning has issues.
    
    Run with: modal run voice_pipeline/modal_deploy.py::start_worker_manual
    """
    import sys
    import subprocess

    sys.path.insert(0, "/root/voice_pipeline")
    os.environ["VOICE_PIPELINE_LOG_LEVEL"] = os.getenv("VOICE_PIPELINE_LOG_LEVEL", "INFO")
    os.environ["VOICE_PIPELINE_LOG_TO_FILE"] = "0"

    print("=" * 60)
    print("🚀 FrontShiftAI Voice Agent Worker (Manual/Global Mode)")
    print("=" * 60)
    print(f"📍 Backend URL: {os.getenv('VOICE_AGENT_BACKEND_URL')}")
    print(f"🎙️  LiveKit URL: {os.getenv('LIVEKIT_URL')}")
    print("This worker will handle ALL rooms that connect to LiveKit")
    print("=" * 60)

    cmd = [
        sys.executable,
        "/root/voice_pipeline/scripts/main.py",
        "start"
    ]

    try:
        subprocess.run(cmd, cwd="/root/voice_pipeline/scripts", check=True, env=os.environ.copy())
    except subprocess.CalledProcessError as e:
        print(f"❌ Worker failed: {e.returncode}")
        raise
    except KeyboardInterrupt:
        print("\n⚠️ Worker interrupted")


# ============================================================================
# CLI Helper
# ============================================================================

@app.local_entrypoint()
def cli():
    """CLI helper with deployment instructions"""
    print("\n" + "=" * 60)
    print("FrontShiftAI Voice Agent - Modal Deployment")
    print("=" * 60)

    print("\n📋 Deploy the API:")
    print("   modal deploy voice_pipeline/modal_deploy.py")

    print("\n🌐 Endpoints (after deploy):")
    print("   GET  /         - Service info")
    print("   GET  /health   - Health check")
    print("   POST /session  - Create session + auto-spawn worker")

    print("\n✨ On-Demand Mode (Default):")
    print("   Workers spawn automatically when user clicks voice button!")
    print("   No manual startup needed - just deploy and go.")

    print("\n🔧 Manual Mode (Backup):")
    print("   If on-demand has issues, run a persistent worker:")
    print("   modal run voice_pipeline/modal_deploy.py::start_worker_manual")

    print("\n" + "=" * 60 + "\n")
