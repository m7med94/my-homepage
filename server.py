import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import ALLOWED_ORIGINS, MUSIC_DIR, validate_production_secrets
from backend.database import init_db
from backend.routers import (
    auth as auth_router,
    telemetry as telemetry_router,
    todos as todos_router,
    music as music_router,
    agent as agent_router,
    network as network_router,
)

# 1. Initialize SQLite database schemas & WAL mode
init_db()

# 2. Validate environment configuration and production secrets
validate_production_secrets()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Automatically start XiaoZhi Cloud MCP WebSocket Bridge in background
    mcp_task = None
    try:
        from backend.mcp_bridge import run_xiaozhi_mcp_bridge, DEFAULT_MCP_URL
        mcp_url = os.getenv("XIAOZHI_MCP_URL", DEFAULT_MCP_URL)
        if mcp_url:
            print(f"[XiaoZhi MCP] Launching background Cloud MCP WebSocket Bridge...")
            mcp_task = asyncio.create_task(run_xiaozhi_mcp_bridge(mcp_url))
    except Exception as e:
        print(f"[XiaoZhi MCP] Startup note: {e}")

    yield

    if mcp_task:
        mcp_task.cancel()

# 2. Initialize FastAPI Application
app = FastAPI(
    title="SensorsHub Core & ESP32 AI Voice Assistant Gateway",
    description="Modular telemetry ingestion, real-time SSE broadcasting, SQLite WAL persistence, Music Streaming, Local Network Tools, and Server-Side AI Copilot.",
    version="2.4.0",
    lifespan=lifespan,
)

# 3. Mount /music directory for direct browser and ESP32 audio streaming
app.mount("/music", StaticFiles(directory=MUSIC_DIR), name="music")

# 4. Browser CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# 5. Include Modular APIRouters
app.include_router(auth_router.router)
app.include_router(telemetry_router.router)
app.include_router(todos_router.router)
app.include_router(music_router.router)
app.include_router(agent_router.router)
app.include_router(network_router.router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"Starting SensorsHub & ESP32 Gateway on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
