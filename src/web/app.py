"""
FastAPI Web Server for Cubey — 2D House Mapping, SLAM Visualizer & Remote Drive Control.

Provides HTTP Basic Auth-protected REST APIs, WebSocket streaming of real-time
occupancy grids and robot pose, and serves the mobile/desktop web application.
"""

import asyncio
import base64
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import config
from src.db.repositories.map_repository import delete_map, list_maps
from src.services.lidar_service import get_lidar_service
from src.services.mapping_service import get_mapping_service
from src.services.wheels_service import get_wheels_service
from src.services.navigation.cubey_nav_service import get_nav_service
from src.services.audio_test_service import get_audio_test_service

logger = logging.getLogger(__name__)

# Locate static folder
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Cubey 2D House Mapping & Remote Control",
    description="Live SLAM floorplan visualizer and teleoperation interface for Cubey Robot",
    version="1.0.0",
)

# CORS middleware for local network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()


# ---------------------------------------------------------------------------
# Authentication Helper
# ---------------------------------------------------------------------------

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth username and password against configured environment."""
    expected_user = config.web_username or "admin"
    expected_pass = config.web_password or "cubey"

    correct_username = secrets.compare_digest(credentials.username, expected_user)
    correct_password = secrets.compare_digest(credentials.password, expected_pass)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def check_auth_token(token: Optional[str] = None) -> bool:
    """Validate query token or password for WebSocket connections."""
    expected_pass = config.web_password or "cubey"
    if token and secrets.compare_digest(token, expected_pass):
        return True
    return False


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class DriveCommandRequest(BaseModel):
    action: str  # forward, backward, strafeLeft, strafeRight, rotateLeft, rotateRight, stop
    speed: Optional[int] = None
    duration_ms: Optional[int] = None
    continuous: bool = False


class StartMappingRequest(BaseModel):
    mode: Optional[str] = "manual"  # "manual" | "autonomous"


class NavGoalRequest(BaseModel):
    x_m: float
    y_m: float
    theta_deg: Optional[float] = 0.0


class SaveMapRequest(BaseModel):
    name: str = "House Floorplan"


class ToggleDenoiserRequest(BaseModel):
    enabled: bool


class RecordTestRequest(BaseModel):
    duration_s: float = 5.0


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/auth/token")
async def get_websocket_token(_: str = Depends(verify_credentials)):
    """Return WebSocket connection token for authenticated browser sessions."""
    return {"token": config.web_password or "cubey"}


@app.get("/api/status")
async def get_system_status(_: str = Depends(verify_credentials)):
    """Return robot battery, motion state, LiDAR telemetry, and SLAM pose."""
    lidar_svc = get_lidar_service()
    mapping_svc = get_mapping_service()
    wheels_svc = get_wheels_service()
    nav_svc = get_nav_service()

    snapshot = mapping_svc.get_snapshot()

    return {
        "status": "online",
        "battery": {
            "percentage": wheels_svc.telemetry.battery_pct,
            "voltage": wheels_svc.telemetry.battery_voltage,
            "is_charging": wheels_svc.telemetry.is_charging,
        },
        "motion": wheels_svc.telemetry.motion,
        "lidar": {
            "is_connected": lidar_svc.is_connected,
            "is_scanning": lidar_svc.is_scanning,
            "is_mock": lidar_svc.is_mock,
            "scan_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
            "min_front_mm": lidar_svc.latest_scan.min_front_dist_mm,
        },
        "mapping": {
            "is_mapping": mapping_svc.is_mapping,
            "map_name": mapping_svc.map_name,
            "active_map_id": mapping_svc.active_map_id,
            "explored_cells": snapshot.total_explored_cells,
            "pose": snapshot.pose.to_dict(),
        },
        "navigation": nav_svc.telemetry.to_dict(),
    }


@app.get("/api/maps")
async def list_house_maps(_: str = Depends(verify_credentials)):
    """List all saved maps stored in SQLite."""
    maps = list_maps(limit=100)
    return [
        {
            "id": m.id,
            "name": m.name,
            "width": m.width,
            "height": m.height,
            "resolution_cm": m.resolution_cm,
            "is_active": m.is_active,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in maps
    ]


@app.post("/api/maps")
async def save_current_map(req: SaveMapRequest, _: str = Depends(verify_credentials)):
    """Save the active occupancy grid map to SQLite."""
    mapping_svc = get_mapping_service()
    map_obj = mapping_svc.save_current_map(name=req.name)
    return {
        "status": "saved",
        "id": map_obj.id,
        "name": map_obj.name,
    }


@app.post("/api/maps/{map_id}/load")
async def load_saved_map(map_id: int, _: str = Depends(verify_credentials)):
    """Load a previously saved map into the SLAM engine."""
    mapping_svc = get_mapping_service()
    success = mapping_svc.load_map(map_id)
    if not success:
        raise HTTPException(status_code=404, detail="Map not found")
    return {"status": "loaded", "map_id": map_id, "name": mapping_svc.map_name}


@app.delete("/api/maps/{map_id}")
async def delete_saved_map(map_id: int, _: str = Depends(verify_credentials)):
    """Delete a saved map from SQLite."""
    success = delete_map(map_id)
    if not success:
        raise HTTPException(status_code=404, detail="Map not found")
    return {"status": "deleted", "map_id": map_id}


@app.post("/api/mapping/start")
async def start_mapping_session(req: Optional[StartMappingRequest] = None, _: str = Depends(verify_credentials)):
    """Start 2D mapping session in manual or autonomous mode."""
    nav_svc = get_nav_service()
    mode = (req.mode if req and req.mode else "manual").lower()

    if mode == "autonomous":
        nav_svc.start_exploration()
    else:
        nav_svc.start_manual_mapping()

    return {"status": "mapping_started", "mode": mode}


@app.post("/api/mapping/pause")
async def pause_mapping_session(_: str = Depends(verify_credentials)):
    """Pause 2D occupancy grid updates and stop autonomous motion."""
    mapping_svc = get_mapping_service()
    nav_svc = get_nav_service()
    nav_svc.stop_navigation()
    mapping_svc.pause_mapping()
    return {"status": "mapping_paused"}


@app.post("/api/navigation/goal")
async def send_navigation_goal(req: NavGoalRequest, _: str = Depends(verify_credentials)):
    """Command robot to navigate toward a 2D floorplan coordinate."""
    nav_svc = get_nav_service()
    success = nav_svc.navigate_to(x_m=req.x_m, y_m=req.y_m, theta_deg=req.theta_deg or 0.0)
    return {"status": "navigating" if success else "failed", "goal": req.dict()}


@app.post("/api/navigation/stop")
async def stop_navigation_command(_: str = Depends(verify_credentials)):
    """Halt active autonomous navigation or exploration."""
    nav_svc = get_nav_service()
    nav_svc.stop_navigation()
    return {"status": "navigation_stopped"}


@app.post("/api/mapping/reset")
async def reset_mapping_grid(_: str = Depends(verify_credentials)):
    """Clear the occupancy grid back to unexplored space."""
    mapping_svc = get_mapping_service()
    mapping_svc.reset_map()
    return {"status": "map_reset"}


@app.post("/api/control/move")
async def send_drive_command(req: DriveCommandRequest, _: str = Depends(verify_credentials)):
    """Drive Cubey's mecanum wheel base."""
    wheels_svc = get_wheels_service()
    if not wheels_svc.is_connected:
        wheels_svc.connect()

    if req.speed is not None:
        wheels_svc.set_speed(req.speed)

    logger.info(
        "HTTP Drive CMD: action=%s, speed=%s, continuous=%s (Wheels connected=%s on %s)",
        req.action,
        req.speed,
        req.continuous,
        wheels_svc.is_connected,
        wheels_svc.port,
    )

    if req.action == "stop":
        wheels_svc.stop()
    elif req.continuous:
        wheels_svc.start_continuous(req.action)
    else:
        wheels_svc.pulse(req.action, req.duration_ms or 250)

    return {"status": "command_sent", "action": req.action, "connected": wheels_svc.is_connected}


@app.post("/api/control/stop")
async def stop_all_motors(_: str = Depends(verify_credentials)):
    """Emergency stop for motors."""
    wheels_svc = get_wheels_service()
    logger.info("HTTP Drive CMD: Emergency STOP")
    wheels_svc.stop()
    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Audio Diagnostics & Microphone Test Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/audio/status")
async def get_audio_test_status(_: str = Depends(verify_credentials)):
    """Return real-time audio test diagnostics and denoiser telemetry."""
    audio_test_svc = get_audio_test_service()
    return audio_test_svc.snapshot.to_dict()


@app.post("/api/audio/denoiser/toggle")
async def toggle_audio_denoiser(req: ToggleDenoiserRequest, _: str = Depends(verify_credentials)):
    """Enable or disable hardware noise suppression."""
    audio_test_svc = get_audio_test_service()
    success = audio_test_svc.set_denoiser_enabled(req.enabled)
    return {"status": "ok", "is_denoiser_enabled": req.enabled, "applied": success}


@app.post("/api/audio/test_recording/start")
async def start_audio_test_recording(req: Optional[RecordTestRequest] = None, _: str = Depends(verify_credentials)):
    """Start a test clip recording for auditory playback."""
    audio_test_svc = get_audio_test_service()
    dur = req.duration_s if req else 5.0
    audio_test_svc.start_test_recording(duration_s=dur)
    return {"status": "recording_started", "duration_s": dur}


@app.get("/api/audio/test_recording/{kind}")
async def download_audio_test_recording(kind: str, _: str = Depends(verify_credentials)):
    """Download or stream the recorded raw or denoised test WAV file."""
    audio_test_svc = get_audio_test_service()
    wav_bytes = audio_test_svc.get_test_wav(kind=kind)
    if not wav_bytes:
        raise HTTPException(status_code=404, detail="No test recording available yet. Click 'Record 5s Clip' first.")
    return Response(content=wav_bytes, media_type="audio/wav")



# ---------------------------------------------------------------------------
# Real-Time WebSocket Streaming
# ---------------------------------------------------------------------------

class WebSocketConnectionManager:
    """Manages active live map WebSocket browser clients."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_json(self, data: Dict[str, Any]):
        disconnected = []
        for ws in self.active_connections:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


ws_manager = WebSocketConnectionManager()


@app.websocket("/ws/live_map")
async def websocket_live_map(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    Real-time bi-directional streaming endpoint:
    - Server -> Client: 10 Hz broadcast of robot pose, trajectory, laser beam points,
      compressed grid, and telemetry.
    - Client -> Server: Teleoperation driving commands (joystick / WASD keys).
    """
    # Verify auth token, cookie, or basic auth header
    expected_pass = config.web_password or "cubey"
    cookie_token = websocket.cookies.get("cubey_auth")

    is_authenticated = False
    if token and secrets.compare_digest(token, expected_pass):
        is_authenticated = True
    elif cookie_token and secrets.compare_digest(cookie_token, expected_pass):
        is_authenticated = True
    else:
        # Fallback to Basic Auth header
        headers = dict(websocket.headers)
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Basic "):
            try:
                raw = base64.b64decode(auth_header[6:]).decode("utf-8")
                user, pwd = raw.split(":", 1)
                if (
                    secrets.compare_digest(user, config.web_username or "admin")
                    and secrets.compare_digest(pwd, expected_pass)
                ):
                    is_authenticated = True
            except Exception:
                pass

    if config.web_password and not is_authenticated:
        logger.warning("Rejected unauthenticated WebSocket connection attempt.")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect(websocket)
    mapping_svc = get_mapping_service()
    wheels_svc = get_wheels_service()
    lidar_svc = get_lidar_service()
    nav_svc = get_nav_service()

    # Background streamer task for this client
    async def _stream_loop():
        frame_counter = 0
        while True:
            try:
                snapshot = mapping_svc.get_snapshot()
                frame_counter += 1

                # Send grid every 2nd frame (~5 Hz) to conserve bandwidth; send pose at 10 Hz
                send_grid = (frame_counter % 2 == 0)
                grid_b64 = (
                    base64.b64encode(mapping_svc.get_compressed_grid()).decode("ascii")
                    if send_grid
                    else None
                )

                payload = {
                    "type": "map_update",
                    "pose": snapshot.pose.to_dict(),
                    "trajectory": snapshot.trajectory,
                    "laser_scan": snapshot.laser_scan,
                    "grid_compressed_b64": grid_b64,
                    "width": snapshot.width,
                    "height": snapshot.height,
                    "resolution_cm": snapshot.resolution_cm,
                    "origin_x_m": snapshot.origin_x_m,
                    "origin_y_m": snapshot.origin_y_m,
                    "is_mapping": snapshot.is_mapping,
                    "map_name": snapshot.map_name,
                    "battery_pct": wheels_svc.telemetry.battery_pct,
                    "motion": wheels_svc.telemetry.motion,
                    "lidar_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
                    "nav_state": nav_svc.telemetry.state,
                    "nav_mode": nav_svc.telemetry.mode,
                    "timestamp": snapshot.timestamp,
                }
                await websocket.send_json(payload)
                await asyncio.sleep(0.10)  # 10 Hz stream
            except Exception:
                break

    stream_task = asyncio.create_task(_stream_loop())

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")

            if mtype == "drive":
                action = msg.get("action", "stop")
                speed = msg.get("speed", 180)
                if not wheels_svc.is_connected:
                    wheels_svc.connect()
                wheels_svc.set_speed(speed)
                logger.info(
                    "WS Drive CMD: %s (speed=%s, continuous=%s, wheels_connected=%s on %s)",
                    action,
                    speed,
                    msg.get("continuous", False),
                    wheels_svc.is_connected,
                    wheels_svc.port,
                )
                if action == "stop":
                    wheels_svc.stop()
                elif msg.get("continuous", False):
                    wheels_svc.start_continuous(action)
                else:
                    wheels_svc.pulse(action, msg.get("duration_ms", 250))

            elif mtype == "stop":
                logger.info("WS Drive CMD: STOP")
                wheels_svc.stop()
                nav_svc.stop_navigation()

            elif mtype == "start_mapping":
                mode = (msg.get("mode") or "manual").lower()
                if mode == "autonomous":
                    nav_svc.start_exploration()
                else:
                    nav_svc.start_manual_mapping()

            elif mtype in ("pause_mapping", "stop_mapping"):
                nav_svc.stop_navigation()
                mapping_svc.pause_mapping()

            elif mtype == "nav_goal":
                x = float(msg.get("x_m", 0.0))
                y = float(msg.get("y_m", 0.0))
                nav_svc.navigate_to(x_m=x, y_m=y)

            elif mtype == "reset_map":
                nav_svc.stop_navigation()
                mapping_svc.reset_map()

    except WebSocketDisconnect:
        pass

    finally:
        stream_task.cancel()
        ws_manager.disconnect(websocket)


@app.websocket("/ws/audio_test")
async def websocket_audio_test(websocket: WebSocket, token: Optional[str] = Query(None)):
    """
    Real-time streaming WebSocket endpoint for live microphone testing:
    - Streams dual VU meters (Raw vs Denoised), dB levels, VAD detection,
      oscilloscope waveform points, and live audio chunks for browser monitoring.
    """
    expected_pass = config.web_password or "cubey"
    cookie_token = websocket.cookies.get("cubey_auth")

    is_authenticated = False
    if token and secrets.compare_digest(token, expected_pass):
        is_authenticated = True
    elif cookie_token and secrets.compare_digest(cookie_token, expected_pass):
        is_authenticated = True
    else:
        headers = dict(websocket.headers)
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Basic "):
            try:
                raw = base64.b64decode(auth_header[6:]).decode("utf-8")
                user, pwd = raw.split(":", 1)
                if (
                    secrets.compare_digest(user, config.web_username or "admin")
                    and secrets.compare_digest(pwd, expected_pass)
                ):
                    is_authenticated = True
            except Exception:
                pass

    if config.web_password and not is_authenticated:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    audio_test_svc = get_audio_test_service()
    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=15)
    loop = asyncio.get_running_loop()

    def _on_audio_telemetry(payload: Dict[str, Any]):
        try:
            loop.call_soon_threadsafe(
                lambda: queue.put_nowait(payload) if not queue.full() else None
            )
        except Exception:
            pass

    audio_test_svc.add_listener(_on_audio_telemetry)

    async def _send_loop():
        while True:
            try:
                payload = await queue.get()
                await websocket.send_json(payload)
            except Exception:
                break

    send_task = asyncio.create_task(_send_loop())

    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "toggle_denoiser":
                enabled = bool(msg.get("enabled", True))
                audio_test_svc.set_denoiser_enabled(enabled)
            elif mtype == "start_record_test":
                dur = float(msg.get("duration_s", 5.0))
                audio_test_svc.start_test_recording(duration_s=dur)
    except WebSocketDisconnect:
        pass
    finally:
        send_task.cancel()
        audio_test_svc.remove_listener(_on_audio_telemetry)



# ---------------------------------------------------------------------------
# Static Web App Mounting
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def serve_index(_: str = Depends(verify_credentials)):
        """Serve the main single-page web app and set session cookie."""
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            response = FileResponse(index_path)
            response.set_cookie(
                key="cubey_auth",
                value=config.web_password or "cubey",
                httponly=True,
                samesite="lax",
            )
            return response
        return {"message": "Cubey Web Interface is running. Place index.html in src/web/static."}
