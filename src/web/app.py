"""
FastAPI Web Server for Cubey — Multi-Page Robot Operations, SLAM Floorplans & Diagnostics.

Mounts modular API routers for system status, maps, navigation, audio diagnostics,
and serves the clean desktop/mobile web portal.
"""

import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.config import config
from src.web.auth import verify_credentials
from src.web.routers.api_system import router as system_router
from src.web.routers.api_maps import router as maps_router
from src.web.routers.api_navigation import router as navigation_router
from src.web.routers.api_audio import router as audio_router
from src.web.routers.ws import router as ws_router

logger = logging.getLogger(__name__)

# Locate static folder
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Cubey Robot Portal",
    description="Live SLAM floorplan visualizer, microphone diagnostics studio, and teleoperation interface for Cubey Robot",
    version="2.0.0",
)

# CORS middleware for local network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount modular routers
app.include_router(system_router)
app.include_router(maps_router)
app.include_router(navigation_router)
app.include_router(audio_router)
app.include_router(ws_router)

# Mount static files and page routes
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _auth_response(filepath: Path) -> FileResponse:
        response = FileResponse(filepath)
        response.set_cookie(
            key="cubey_auth",
            value=config.web_password or "cubey",
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/")
    async def serve_home(_: str = Depends(verify_credentials)):
        """Serve the Home Dashboard portal."""
        index_path = STATIC_DIR / "index.html"
        return _auth_response(index_path)

    @app.get("/map")
    async def serve_map_page(_: str = Depends(verify_credentials)):
        """Serve the dedicated 2D SLAM House Mapping & Remote Control page."""
        map_path = STATIC_DIR / "map.html"
        return _auth_response(map_path)

    @app.get("/mic-test")
    async def serve_mic_test_page(_: str = Depends(verify_credentials)):
        """Serve the dedicated Microphone Diagnostics & Denoiser Studio page."""
        mic_path = STATIC_DIR / "mic_test.html"
        return _auth_response(mic_path)
