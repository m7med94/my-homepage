# server.py — Modular ESP32 AI Voice Assistant & SensorsHub Gateway
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import ALLOWED_ORIGINS, MUSIC_DIR
from backend.database import init_db
from backend.routers import (
    auth as auth_router,
    telemetry as telemetry_router,
    todos as todos_router,
    music as music_router,
    agent as agent_router,
)

# 1. Initialize SQLite database schemas & WAL mode
init_db()

# 2. Initialize FastAPI Application
app = FastAPI(
    title="SensorsHub Core & ESP32 AI Voice Assistant Gateway",
    description="Modular telemetry ingestion, real-time SSE broadcasting, SQLite WAL persistence, Music Streaming, and Server-Side AI Copilot.",
    version="2.2.0",
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"Starting SensorsHub & ESP32 Gateway on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
