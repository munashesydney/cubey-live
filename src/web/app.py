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


def _get_ros_mapping_backend():
    """Return the ROS mapping bridge only when the replacement stack is enabled."""
    if not config.ros2_enabled:
        return None
    from src.services.ros_bridge_service import get_ros_bridge_service

    return get_ros_bridge_service()


def _start_ros_mapping() -> Dict[str, Any]:
    bridge = _get_ros_mapping_backend()
    if bridge is None:
        raise RuntimeError("ROS mapping backend is disabled")
    lidar_svc = get_lidar_service()
    if not lidar_svc.is_connected and not lidar_svc.connect():
        raise HTTPException(status_code=503, detail="LiDAR connection failed")
    if not lidar_svc.is_scanning and not lidar_svc.start_scan():
        raise HTTPException(status_code=503, detail="LiDAR scan failed to start")
    bridge.set_mapping_enabled(True)
    return bridge.status()


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
    wheels_svc = get_wheels_service()

    ros_bridge = _get_ros_mapping_backend()
    mapping_svc = None if ros_bridge else get_mapping_service()
    snapshot = mapping_svc.get_snapshot() if mapping_svc else None
    ros2_status = ros_bridge.status() if ros_bridge else None
    ros_snapshot = ros_bridge.mapping_snapshot() if ros_bridge else None
    if ros_snapshot:
        mapping_status = {
            "is_mapping": ros_snapshot["is_mapping"],
            "autonomy_enabled": bool(config.ros2_command_output_enabled),
            "map_name": "ROS 2 Live Floorplan",
            "active_map_id": None,
            "explored_cells": ros_snapshot["total_explored_cells"],
            "pose": ros_snapshot["pose"],
            "localization": {
                "backend": "slam_toolbox",
                "map_age_s": ros2_status["map_receive_age_s"],
            },
            "navigation": ros_snapshot["navigation"],
        }
    else:
        mapping_status = {
            "is_mapping": False if ros_bridge else mapping_svc.is_mapping,
            "autonomy_enabled": (
                bool(config.ros2_command_output_enabled)
                if ros_bridge
                else mapping_svc.autonomy_enabled
            ),
            "map_name": "ROS 2 Live Floorplan" if ros_bridge else mapping_svc.map_name,
            "active_map_id": None if ros_bridge else mapping_svc.active_map_id,
            "explored_cells": 0 if ros_bridge else snapshot.total_explored_cells,
            "pose": (
                {"x_m": 0.0, "y_m": 0.0, "theta_deg": 0.0, "timestamp": 0.0}
                if ros_bridge
                else snapshot.pose.to_dict()
            ),
            "localization": (
                {"backend": "slam_toolbox", "state": "waiting_for_map"}
                if ros_bridge
                else mapping_svc.localization_status()
            ),
            "navigation": (
                ros2_status["exploration"]
                if ros_bridge
                else mapping_svc.navigator.status()
            ),
        }

    return {
        "status": "online",
        "battery": {
            "percentage": wheels_svc.telemetry.battery_pct,
            "voltage": wheels_svc.telemetry.battery_voltage,
            "is_charging": wheels_svc.telemetry.is_charging,
        },
        "motion": wheels_svc.telemetry.motion,
        "ros2": ros2_status,
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
        "mapping": mapping_status,
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
    if config.ros2_enabled:
        ros2_status = _start_ros_mapping()
        return {
            "status": "mapping_started",
            "autonomous_started": bool(config.ros2_command_output_enabled),
            "autonomy_enabled": bool(config.ros2_command_output_enabled),
            "navigation": ros2_status["exploration"],
        }
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
    ros_bridge = _get_ros_mapping_backend()
    if ros_bridge:
        ros_bridge.set_mapping_enabled(False)
        get_wheels_service().stop()
        return {"status": "mapping_paused"}
    mapping_svc = get_mapping_service()
    mapping_svc.pause_mapping()
    return {"status": "mapping_paused"}


@app.post("/api/mapping/reset")
async def reset_mapping_grid(_: str = Depends(verify_credentials)):
    """Clear the occupancy grid back to unexplored space."""
    ros_bridge = _get_ros_mapping_backend()
    if ros_bridge:
        if not ros_bridge.reset_mapping():
            raise HTTPException(status_code=503, detail="ROS mapping reset unavailable")
        return {"status": "map_reset"}
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

    ros_bridge = _get_ros_mapping_backend()
    if ros_bridge:
        # Manual commands intentionally behave like the wheels page. Stop ROS
        # exploration first so two command sources can never fight each other.
        if req.action != "stop":
            ros_bridge.set_mapping_enabled(False)
    else:
        mapping_svc = get_mapping_service()
        safe, safety_reason = mapping_svc.navigator.authorize_manual_motion(req.action)
        if not safe:
            raise HTTPException(status_code=409, detail=safety_reason)
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
    logger.info("HTTP Drive CMD: Emergency STOP")
    ros_bridge = _get_ros_mapping_backend()
    if ros_bridge:
        ros_bridge.set_mapping_enabled(False)
        get_wheels_service().emergency_stop("operator_http_estop")
    else:
        get_mapping_service().navigator.emergency_stop("operator_http_estop")
    return {"status": "emergency_stopped", "latched": True}


@app.post("/api/control/estop/reset")
async def reset_emergency_stop(_: str = Depends(verify_credentials)):
    """Explicitly re-arm motion after all safety preconditions pass."""
    ros_bridge = _get_ros_mapping_backend()
    if ros_bridge:
        wheels_svc = get_wheels_service()
        cleared = wheels_svc.clear_emergency_stop()
        reason = "cleared" if cleared else wheels_svc.emergency_stop_reason
    else:
        cleared, reason = get_mapping_service().navigator.clear_emergency_stop()
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
    wheels_svc = get_wheels_service()
    lidar_svc = get_lidar_service()
    ros_bridge = _get_ros_mapping_backend()
    mapping_svc = None if ros_bridge else get_mapping_service()

    # Background streamer task for this client
    async def _stream_loop():
        frame_counter = 0
        while True:
            try:
                frame_counter += 1
                ros_snapshot = ros_bridge.mapping_snapshot() if ros_bridge else None

                # Send grid every 2nd frame (~5 Hz) to conserve bandwidth; send pose at 10 Hz
                send_grid = (frame_counter % 2 == 0)
                if ros_bridge:
                    ros_status = ros_bridge.status()
                    nav_status = (
                        ros_snapshot["navigation"]
                        if ros_snapshot
                        else ros_status["exploration"]
                    )
                    payload = {
                        "type": "map_update",
                        "pose": (
                            ros_snapshot["pose"]
                            if ros_snapshot
                            else {"x_m": 0.0, "y_m": 0.0, "theta_deg": 0.0, "timestamp": 0.0}
                        ),
                        "trajectory": ros_snapshot["trajectory"] if ros_snapshot else [],
                        "laser_scan": ros_snapshot["laser_scan"] if ros_snapshot else [],
                        "grid_compressed_b64": (
                            base64.b64encode(ros_bridge.get_compressed_grid()).decode("ascii")
                            if send_grid and ros_snapshot
                            else None
                        ),
                        "width": ros_snapshot["width"] if ros_snapshot else 0,
                        "height": ros_snapshot["height"] if ros_snapshot else 0,
                        "resolution_cm": ros_snapshot["resolution_cm"] if ros_snapshot else 5.0,
                        "origin_x_m": ros_snapshot["origin_x_m"] if ros_snapshot else 0.0,
                        "origin_y_m": ros_snapshot["origin_y_m"] if ros_snapshot else 0.0,
                        "is_mapping": ros_status["mapping_enabled"],
                        "autonomy_enabled": bool(config.ros2_command_output_enabled),
                        "map_name": "ROS 2 Live Floorplan",
                        "planned_path": [],
                        "target_frontier": ros_snapshot["target_frontier"] if ros_snapshot else None,
                        "nav_state": nav_status.get("state", "WAITING_FOR_ROS"),
                        "nav_status": nav_status,
                        "localization": {
                            "backend": "slam_toolbox",
                            "map_age_s": ros_status["map_receive_age_s"],
                        },
                        "timestamp": ros_snapshot["timestamp"] if ros_snapshot else 0.0,
                    }
                else:
                    snapshot = mapping_svc.get_snapshot()
                    payload = {
                        "type": "map_update",
                        "pose": snapshot.pose.to_dict(),
                        "trajectory": snapshot.trajectory,
                        "laser_scan": snapshot.laser_scan,
                        "grid_compressed_b64": (
                            base64.b64encode(mapping_svc.get_compressed_grid()).decode("ascii")
                            if send_grid
                            else None
                        ),
                        "width": snapshot.width,
                        "height": snapshot.height,
                        "resolution_cm": snapshot.resolution_cm,
                        "origin_x_m": snapshot.origin_x_m,
                        "origin_y_m": snapshot.origin_y_m,
                        "is_mapping": snapshot.is_mapping,
                        "autonomy_enabled": mapping_svc.autonomy_enabled,
                        "map_name": snapshot.map_name,
                        "planned_path": snapshot.planned_path,
                        "target_frontier": snapshot.target_frontier,
                        "nav_state": mapping_svc.navigator.state.value,
                        "nav_status": mapping_svc.navigator.status(),
                        "localization": mapping_svc.localization_status(),
                        "timestamp": snapshot.timestamp,
                    }
                payload.update({
                    "battery_pct": wheels_svc.telemetry.battery_pct,
                    "motion": wheels_svc.telemetry.motion,
                    "lidar_rate_hz": lidar_svc.latest_scan.scan_rate_hz,
                })
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
                if ros_bridge:
                    if action != "stop":
                        ros_bridge.set_mapping_enabled(False)
                else:
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

                if not ros_bridge:
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
                if ros_bridge:
                    ros_bridge.set_mapping_enabled(False)
                    wheels_svc.emergency_stop("operator_websocket_estop")
                else:
                    mapping_svc.navigator.emergency_stop("operator_websocket_estop")

            elif mtype == "reset_estop":
                if ros_bridge:
                    cleared = wheels_svc.clear_emergency_stop()
                    reason = "cleared" if cleared else wheels_svc.emergency_stop_reason
                else:
                    cleared, reason = mapping_svc.navigator.clear_emergency_stop()
                await websocket.send_json({"type": "estop_reset", "cleared": cleared, "reason": reason})

            elif mtype == "start_mapping":
                if ros_bridge:
                    _start_ros_mapping()
                else:
                    mapping_svc.start_mapping()

            elif mtype == "pause_mapping":
                if ros_bridge:
                    ros_bridge.set_mapping_enabled(False)
                    wheels_svc.stop()
                else:
                    mapping_svc.pause_mapping()

            elif mtype == "reset_map":
                if ros_bridge:
                    ros_bridge.reset_mapping()
                else:
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
