import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import ALLOWED_ORIGINS, validate_production_secrets
from backend.database import init_db
from backend.routers import (
    auth as auth_router,
    telemetry as telemetry_router,
    todos as todos_router,
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
    try:
        from backend.mcp_bridge import gateway_manager
        print(f"[XiaoZhi MCP] Initializing background Gateway Manager...")
        await gateway_manager.set_active(True)
    except Exception as e:
        print(f"[XiaoZhi MCP] Startup note: {e}")

    yield

    try:
        from backend.mcp_bridge import gateway_manager
        await gateway_manager.set_active(False)
    except Exception:
        pass

# 2. Initialize FastAPI Application
app = FastAPI(
    title="SensorsHub Core & ESP32 AI Voice Assistant Gateway",
    description="Modular telemetry ingestion, real-time SSE broadcasting, SQLite WAL persistence, Local Network Tools, and Server-Side AI Copilot.",
    version="2.5.0",
    lifespan=lifespan,
)

# 3. Browser CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# 4. Include Modular APIRouters
app.include_router(auth_router.router)
app.include_router(telemetry_router.router)
app.include_router(todos_router.router)
app.include_router(agent_router.router)
app.include_router(network_router.router)

# 5. Serve Frontend Static Pages & Assets
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/styles.css")
async def serve_styles():
    return FileResponse(os.path.join(BASE_DIR, "styles.css"), media_type="text/css")

@app.get("/script.js")
async def serve_script():
    return FileResponse(os.path.join(BASE_DIR, "script.js"), media_type="application/javascript")

@app.get("/{page_name}.html")
async def serve_html(page_name: str):
    file_path = os.path.join(BASE_DIR, f"{page_name}.html")
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"Starting SensorsHub & ESP32 Gateway on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)

