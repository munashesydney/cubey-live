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
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import config
from src.db.repositories.map_repository import delete_map, list_maps
from src.services.lidar_service import get_lidar_service
from src.services.mapping_service import get_mapping_service
from src.services.wheels_service import get_wheels_service

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

    user_match = secrets.compare_digest(credentials.username, expected_user)
    pass_match = secrets.compare_digest(credentials.password, expected_pass)

    if not (user_match and pass_match):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
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
    speed: Optional[int] = 180
    duration_ms: Optional[int] = 250
    continuous: Optional[bool] = False


class SaveMapRequest(BaseModel):
    name: str = "House Floorplan"


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

    snapshot = mapping_svc.get_snapshot()

    return {
        "status": "online",
        "battery": {
            "percentage": wheels_svc.telemetry.battery_pct,
            "voltage": wheels_svc.telemetry.battery_voltage,
            "is_charging": wheels_svc.telemetry.is_charging,
        },
        "motion": wheels_svc.telemetry.motion,
        "emergency_stop": {
            "latched": wheels_svc.is_emergency_stopped,
            "reason": wheels_svc.emergency_stop_reason,
        },
        "lidar": {
            "is_connected": lidar_svc.is_connected,
            "is_scanning": lidar_svc.is_scanning,
            "is_mock": lidar_svc.is_mock,
            "scan_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
            "min_front_mm": lidar_svc.latest_scan.min_front_dist_mm,
        },
        "mapping": {
            "is_mapping": mapping_svc.is_mapping,
            "autonomy_enabled": mapping_svc.autonomy_enabled,
            "map_name": mapping_svc.map_name,
            "active_map_id": mapping_svc.active_map_id,
            "explored_cells": snapshot.total_explored_cells,
            "pose": snapshot.pose.to_dict(),
            "localization": mapping_svc.localization_status(),
            "navigation": mapping_svc.navigator.status(),
        },
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
async def start_mapping_session(_: str = Depends(verify_credentials)):
    """Start active 2D occupancy grid updates."""
    mapping_svc = get_mapping_service()
    autonomous_started = mapping_svc.start_mapping()
    return {
        "status": "mapping_started",
        "autonomous_started": autonomous_started,
        "autonomy_enabled": mapping_svc.autonomy_enabled,
        "navigation": mapping_svc.navigator.status(),
    }


@app.post("/api/mapping/pause")
async def pause_mapping_session(_: str = Depends(verify_credentials)):
    """Pause 2D occupancy grid updates."""
    mapping_svc = get_mapping_service()
    mapping_svc.pause_mapping()
    return {"status": "mapping_paused"}


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
    if wheels_svc.is_emergency_stopped and req.action != "stop":
        raise HTTPException(
            status_code=423,
            detail=f"Emergency stop is latched: {wheels_svc.emergency_stop_reason}",
        )

    mapping_svc = get_mapping_service()
    safe, safety_reason = mapping_svc.navigator.authorize_manual_motion(req.action)
    if not safe:
        raise HTTPException(status_code=409, detail=safety_reason)

    # Yield autonomous exploration to manual teleoperation
    if hasattr(mapping_svc, "navigator"):
        mapping_svc.navigator.yield_to_teleop(3.0)

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
        sent = wheels_svc.stop()
    elif req.continuous:
        sent = wheels_svc.start_continuous(req.action)
    else:
        sent = wheels_svc.pulse(req.action, req.duration_ms or 250)

    if not sent:
        raise HTTPException(status_code=503, detail="Wheel command was rejected")

    return {"status": "command_sent", "action": req.action, "connected": wheels_svc.is_connected}


@app.post("/api/control/stop")
async def stop_all_motors(_: str = Depends(verify_credentials)):
    """Latch emergency stop across navigator, host service, and ESP32."""
    mapping_svc = get_mapping_service()
    logger.info("HTTP Drive CMD: Emergency STOP")
    mapping_svc.navigator.emergency_stop("operator_http_estop")
    return {"status": "emergency_stopped", "latched": True}


@app.post("/api/control/estop/reset")
async def reset_emergency_stop(_: str = Depends(verify_credentials)):
    """Explicitly re-arm motion after all safety preconditions pass."""
    navigator = get_mapping_service().navigator
    cleared, reason = navigator.clear_emergency_stop()
    if not cleared:
        raise HTTPException(status_code=409, detail=reason)
    return {"status": "emergency_stop_cleared", "latched": False}


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
                    "autonomy_enabled": mapping_svc.autonomy_enabled,
                    "map_name": snapshot.map_name,
                    "battery_pct": wheels_svc.telemetry.battery_pct,
                    "motion": wheels_svc.telemetry.motion,
                    "lidar_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
                    "planned_path": snapshot.planned_path,
                    "target_frontier": snapshot.target_frontier,
                    "nav_state": mapping_svc.navigator.state.value if hasattr(mapping_svc, "navigator") else "IDLE",
                    "nav_status": mapping_svc.navigator.status() if hasattr(mapping_svc, "navigator") else {},
                    "localization": mapping_svc.localization_status(),
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
                safe, safety_reason = mapping_svc.navigator.authorize_manual_motion(action)
                if not safe:
                    logger.warning(
                        "WS Drive CMD rejected: %s reason=%s (wheels_connected=%s on %s)",
                        action,
                        safety_reason,
                        wheels_svc.is_connected,
                        wheels_svc.port,
                    )
                    await websocket.send_json({"type": "command_rejected", "reason": safety_reason})
                    continue
                wheels_svc.set_speed(speed)

                if hasattr(mapping_svc, "navigator"):
                    mapping_svc.navigator.yield_to_teleop(3.0)

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
                    if not wheels_svc.start_continuous(action):
                        await websocket.send_json({"type": "command_rejected", "reason": wheels_svc.emergency_stop_reason})
                else:
                    if not wheels_svc.pulse(action, msg.get("duration_ms", 250)):
                        await websocket.send_json({"type": "command_rejected", "reason": wheels_svc.emergency_stop_reason})

            elif mtype == "stop":
                logger.info("WS Drive CMD: Emergency STOP")
                mapping_svc.navigator.emergency_stop("operator_websocket_estop")

            elif mtype == "reset_estop":
                cleared, reason = mapping_svc.navigator.clear_emergency_stop()
                await websocket.send_json({"type": "estop_reset", "cleared": cleared, "reason": reason})

            elif mtype == "start_mapping":
                mapping_svc.start_mapping()

            elif mtype == "pause_mapping":
                mapping_svc.pause_mapping()

            elif mtype == "reset_map":
                mapping_svc.reset_map()

    except WebSocketDisconnect:
        pass
    finally:
        stream_task.cancel()
        ws_manager.disconnect(websocket)


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
