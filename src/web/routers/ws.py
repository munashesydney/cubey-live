"""
Real-time WebSocket streaming endpoints for Live 2D SLAM Maps and Microphone Audio Test.
"""

import asyncio
import base64
import json
import logging
import os
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


def _is_ws_authenticated(websocket: WebSocket, token: Optional[str] = None) -> bool:
    expected_pass = config.web_password or "cubey"
    cookie_token = websocket.cookies.get("cubey_auth")

    if token and secrets.compare_digest(token, expected_pass):
        return True
    if cookie_token and secrets.compare_digest(cookie_token, expected_pass):
        return True

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
                return True
        except Exception:
            pass

    return not bool(config.web_password)


@router.websocket("/ws/live_map")
async def websocket_live_map(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Real-time streaming WebSocket endpoint for 2D House Floorplan & Telemetry."""
    if not _is_ws_authenticated(websocket, token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws_manager.connect(websocket)

    mapping_svc = get_mapping_service()
    lidar_svc = get_lidar_service()
    wheels_svc = get_wheels_service()
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

                # If ROS 2 SLAM Toolbox live map is exported to IPC, overlay it seamlessly
                nav2_map_file = "/tmp/cubey_nav2_live_map.json"
                if os.path.exists(nav2_map_file):
                    try:
                        with open(nav2_map_file, "r") as f:
                            nav2_data = json.load(f)
                        if "pose" in nav2_data and nav2_data["pose"]:
                            payload["pose"] = nav2_data["pose"]
                        if "trajectory" in nav2_data and nav2_data["trajectory"]:
                            payload["trajectory"] = nav2_data["trajectory"]
                        if send_grid and "grid_compressed_b64" in nav2_data and nav2_data["grid_compressed_b64"]:
                            payload["grid_compressed_b64"] = nav2_data["grid_compressed_b64"]
                            payload["width"] = nav2_data.get("width", payload["width"])
                            payload["height"] = nav2_data.get("height", payload["height"])
                            payload["resolution_cm"] = nav2_data.get("resolution_cm", payload["resolution_cm"])
                            payload["origin_x_m"] = nav2_data.get("origin_x_m", payload["origin_x_m"])
                            payload["origin_y_m"] = nav2_data.get("origin_y_m", payload["origin_y_m"])
                    except Exception:
                        pass

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
                if action == "stop":
                    wheels_svc.stop()
                elif msg.get("continuous", False):
                    wheels_svc.start_continuous(action)
                else:
                    wheels_svc.pulse(action, msg.get("duration_ms", 250))

            elif mtype == "stop":
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
    if not _is_ws_authenticated(websocket, token):
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
