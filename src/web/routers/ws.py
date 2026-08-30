"""
Real-time WebSocket streaming endpoints for Live 2D SLAM Maps and Microphone Audio Test.
"""

import asyncio
import base64
import logging
import secrets
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from src.config import config
from src.services.audio_test_service import get_audio_test_service
from src.services.lidar_service import get_lidar_service
from src.services.mapping_service import get_mapping_service
from src.services.wheels_service import get_wheels_service
from src.services.navigation.cubey_nav_service import get_nav_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websockets"])


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


@router.websocket("/ws/live_map")
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


@router.websocket("/ws/audio_test")
async def websocket_audio_test(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Real-time streaming WebSocket endpoint for live microphone testing."""
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
