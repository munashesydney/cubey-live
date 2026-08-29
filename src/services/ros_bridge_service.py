"""Fail-closed bridge between Cubey's host hardware services and ROS 2."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
import zlib
from typing import Any, Dict, Optional

from src.services.lidar_service import LidarScanData, get_lidar_service
from src.services.wheels_service import TelemetryData, get_wheels_service

logger = logging.getLogger(__name__)

try:
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect as websocket_connect

    WEBSOCKETS_SYNC_AVAILABLE = True
except ImportError:
    ConnectionClosed = Exception
    websocket_connect = None
    WEBSOCKETS_SYNC_AVAILABLE = False


class RosBridgeService:
    """Publish sensors to ROS and consume final, watchdog-bounded velocity commands."""

    _SCAN_TOPIC = "/scan"
    _EXECUTED_VELOCITY_TOPIC = "/cubey/executed_cmd_vel"
    _COMMAND_TOPIC = "/cmd_vel"
    _EXPLORATION_ENABLED_TOPIC = "/cubey/exploration/enabled"
    _MAPPING_RESET_TOPIC = "/cubey/mapping/reset"
    _LASER_BINS = 720

    def __init__(
        self,
        *,
        url: str,
        command_output_enabled: bool = False,
        max_forward_mps: float = 0.18,
        max_strafe_mps: float = 0.15,
        max_angular_rps: float = 0.70,
        command_timeout_s: float = 0.35,
        lidar_service=None,
        wheels_service=None,
    ) -> None:
        self.url = url
        self.command_output_enabled = bool(command_output_enabled)
        self.max_forward_mps = max(0.01, float(max_forward_mps))
        self.max_strafe_mps = max(0.01, float(max_strafe_mps))
        self.max_angular_rps = max(0.01, float(max_angular_rps))
        self.command_timeout_s = max(0.10, float(command_timeout_s))
        self.lidar_service = lidar_service or get_lidar_service()
        self.wheels_service = wheels_service or get_wheels_service()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connection = None
        self._connection_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._connected = False
        self._last_error = ""
        self._last_scan_published_at = 0.0
        self._last_executed_velocity_at = 0.0
        self._last_command_received_at = 0.0
        self._last_command = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self._last_applied_twist = dict(self._last_command)
        self._watchdog_stopped = True
        self._mapping_enabled = False
        self._exploration_status: Dict[str, Any] = {
            "enabled": False,
            "state": "WAITING_FOR_ROS",
            "reason": "not_connected",
        }
        self._map_snapshot: Optional[Dict[str, Any]] = None
        self._compressed_grid = zlib.compress(b"")
        self._pose = {
            "x_m": 0.0,
            "y_m": 0.0,
            "theta_deg": 0.0,
            "timestamp": 0.0,
        }
        self._trajectory = []
        self._laser_hits = []
        self._target_frontier = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def status(self) -> Dict[str, Any]:
        now = time.monotonic()

        def age(timestamp: float) -> Optional[float]:
            return round(now - timestamp, 3) if timestamp > 0.0 else None

        with self._state_lock:
            exploration_status = dict(self._exploration_status)
            map_snapshot = dict(self._map_snapshot) if self._map_snapshot else None
        return {
            "enabled": self._running,
            "connected": self._connected,
            "url": self.url,
            "command_output_enabled": self.command_output_enabled,
            "last_error": self._last_error,
            "scan_publish_age_s": age(self._last_scan_published_at),
            "executed_velocity_publish_age_s": age(
                self._last_executed_velocity_at
            ),
            "command_receive_age_s": age(self._last_command_received_at),
            "last_command": dict(self._last_command),
            "mapping_enabled": self._mapping_enabled,
            "exploration": exploration_status,
            "map_receive_age_s": age(
                map_snapshot["received_at"] if map_snapshot else 0.0
            ),
        }

    def mapping_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            if self._map_snapshot is None:
                return None
            snapshot = dict(self._map_snapshot)
            snapshot["pose"] = dict(self._pose)
            snapshot["trajectory"] = list(self._trajectory)
            snapshot["laser_scan"] = list(self._laser_hits)
            snapshot["target_frontier"] = self._target_frontier
            snapshot["navigation"] = dict(self._exploration_status)
            snapshot["is_mapping"] = self._mapping_enabled
            return snapshot

    def get_compressed_grid(self) -> bytes:
        with self._state_lock:
            return self._compressed_grid

    def set_mapping_enabled(self, enabled: bool) -> bool:
        self._mapping_enabled = bool(enabled)
        return self._send(
            {
                "op": "publish",
                "topic": self._EXPLORATION_ENABLED_TOPIC,
                "msg": {"data": self._mapping_enabled},
            }
        )

    def reset_mapping(self) -> bool:
        with self._state_lock:
            self._map_snapshot = None
            self._compressed_grid = zlib.compress(b"")
            self._trajectory = []
            self._laser_hits = []
            self._target_frontier = None
        published = self._send(
            {
                "op": "publish",
                "topic": self._MAPPING_RESET_TOPIC,
                "msg": {"data": True},
            }
        )
        called = self._send(
            {
                "op": "call_service",
                "id": "cubey-clear-slam",
                "service": "/slam_toolbox/clear",
                "args": {},
            }
        )
        return published and called

    def start(self) -> bool:
        if self._running:
            return True
        if not WEBSOCKETS_SYNC_AVAILABLE:
            self._last_error = "websockets_sync_client_unavailable"
            logger.error("ROS bridge unavailable: websockets>=12 sync client missing")
            return False
        self._running = True
        self.lidar_service.add_scan_listener(self._publish_scan)
        self.wheels_service.add_telemetry_listener(self._publish_executed_velocity)
        self._thread = threading.Thread(
            target=self._connection_loop,
            daemon=True,
            name="RosBridgeClient",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        self.lidar_service.remove_scan_listener(self._publish_scan)
        self.wheels_service.remove_telemetry_listener(
            self._publish_executed_velocity
        )
        self._stop_motion_once()
        with self._connection_lock:
            connection = self._connection
            self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._connected = False

    def _connection_loop(self) -> None:
        while self._running:
            try:
                assert websocket_connect is not None
                with websocket_connect(
                    self.url,
                    open_timeout=3.0,
                    close_timeout=1.0,
                ) as connection:
                    with self._connection_lock:
                        self._connection = connection
                    self._connected = True
                    self._last_error = ""
                    self._watchdog_stopped = True
                    self._register_topics()
                    logger.info("Connected host hardware bridge to ROS at %s", self.url)

                    while self._running:
                        try:
                            raw_message = connection.recv(timeout=0.10)
                        except TimeoutError:
                            raw_message = None
                        if raw_message:
                            self._handle_rosbridge_message(raw_message)
                        self._enforce_command_watchdog()
            except (ConnectionClosed, OSError, TimeoutError) as exc:
                self._last_error = f"{type(exc).__name__}:{exc}"
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}:{exc}"
                logger.exception("ROS bridge client error")
            finally:
                self._connected = False
                with self._connection_lock:
                    self._connection = None
                self._stop_motion_once()

            if self._running:
                time.sleep(1.0)

    def _register_topics(self) -> None:
        self._send(
            {
                "op": "advertise",
                "topic": self._SCAN_TOPIC,
                "type": "sensor_msgs/msg/LaserScan",
            }
        )
        for topic in (
            self._EXPLORATION_ENABLED_TOPIC,
            self._MAPPING_RESET_TOPIC,
        ):
            self._send(
                {
                    "op": "advertise",
                    "topic": topic,
                    "type": "std_msgs/msg/Bool",
                }
            )
        self._send(
            {
                "op": "advertise",
                "topic": self._EXECUTED_VELOCITY_TOPIC,
                "type": "geometry_msgs/msg/Twist",
            }
        )
        subscriptions = (
            ("cubey-map", "/map", "nav_msgs/msg/OccupancyGrid", 250),
            ("cubey-pose", "/cubey/pose", "geometry_msgs/msg/PoseStamped", 50),
            ("cubey-trajectory", "/cubey/trajectory", "nav_msgs/msg/Path", 250),
            ("cubey-frontier-goal", "/cubey/exploration/goal", "geometry_msgs/msg/PoseStamped", 100),
            ("cubey-exploration-status", "/cubey/exploration/status", "std_msgs/msg/String", 100),
        )
        for subscription_id, topic, message_type, throttle_rate in subscriptions:
            self._send(
                {
                    "op": "subscribe",
                    "id": subscription_id,
                    "topic": topic,
                    "type": message_type,
                    "throttle_rate": throttle_rate,
                    "queue_length": 1,
                }
            )
        self.set_mapping_enabled(self._mapping_enabled)
        self._send(
            {
                "op": "subscribe",
                "id": "cubey-cmd-vel",
                "topic": self._COMMAND_TOPIC,
                "type": "geometry_msgs/msg/Twist",
                "throttle_rate": 0,
                "queue_length": 1,
            }
        )

    def _send(self, payload: Dict[str, Any]) -> bool:
        with self._connection_lock:
            connection = self._connection
        if connection is None:
            return False
        try:
            encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
            with self._send_lock:
                connection.send(encoded)
            return True
        except Exception as exc:
            self._last_error = f"send_failed:{type(exc).__name__}:{exc}"
            return False

    @staticmethod
    def _stamp(timestamp: float) -> Dict[str, int]:
        seconds = int(timestamp)
        return {
            "sec": seconds,
            "nanosec": int((timestamp - seconds) * 1_000_000_000),
        }

    def _publish_scan(self, scan: LidarScanData) -> None:
        if not self._connected:
            return
        ranges = [0.0] * self._LASER_BINS
        intensities = [0.0] * self._LASER_BINS
        increment = 2.0 * math.pi / self._LASER_BINS

        for point in scan.points:
            if point.quality <= 0 or point.distance_mm <= 0.0:
                continue
            # Cubey angles are clockwise from forward. ROS angles are
            # counter-clockwise from forward (+X), so the sign is inverted.
            ros_angle = -math.radians(point.angle_deg)
            ros_angle = (ros_angle + math.pi) % (2.0 * math.pi) - math.pi
            index = int(round((ros_angle + math.pi) / increment))
            index = max(0, min(self._LASER_BINS - 1, index))
            distance_m = point.distance_mm / 1000.0
            if ranges[index] == 0.0 or distance_m < ranges[index]:
                ranges[index] = round(distance_m, 4)
                intensities[index] = float(point.quality)

        scan_time = 1.0 / scan.scan_rate_hz if scan.scan_rate_hz > 0.0 else 0.1
        published = self._send(
            {
                "op": "publish",
                "topic": self._SCAN_TOPIC,
                "msg": {
                    "header": {
                        "stamp": self._stamp(scan.timestamp),
                        "frame_id": "laser",
                    },
                    "angle_min": -math.pi,
                    "angle_max": math.pi - increment,
                    "angle_increment": increment,
                    "time_increment": scan_time / self._LASER_BINS,
                    "scan_time": scan_time,
                    "range_min": max(
                        0.01,
                        self.lidar_service.min_valid_distance_mm / 1000.0,
                    ),
                    "range_max": 12.0,
                    "ranges": ranges,
                    "intensities": intensities,
                },
            }
        )
        if published:
            self._last_scan_published_at = time.monotonic()
        self._update_laser_hits(scan)

    def _publish_executed_velocity(self, telemetry: TelemetryData) -> None:
        if not self._connected:
            return
        velocity = self._velocity_from_telemetry(telemetry)
        if self._send(
            {
                "op": "publish",
                "topic": self._EXECUTED_VELOCITY_TOPIC,
                "msg": {
                    "linear": {
                        "x": velocity["linear_x"],
                        "y": velocity["linear_y"],
                        "z": 0.0,
                    },
                    "angular": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": velocity["angular_z"],
                    },
                },
            }
        ):
            self._last_executed_velocity_at = time.monotonic()

    def _velocity_from_telemetry(self, telemetry: TelemetryData) -> Dict[str, float]:
        motion = telemetry.motion.strip().upper()
        if motion in {"VELOCITY_CONTROL", "TWIST"}:
            return dict(self._last_applied_twist)

        speed_ratio = max(0.0, min(2.0, telemetry.speed / 90.0))
        forward = self.max_forward_mps * speed_ratio
        strafe = self.max_strafe_mps * speed_ratio
        angular = self.max_angular_rps * speed_ratio
        diagonal = math.sqrt(0.5)
        motions = {
            "FORWARD": (forward, 0.0, 0.0),
            "BACKWARD": (-forward, 0.0, 0.0),
            "STRAFE_LEFT": (0.0, strafe, 0.0),
            "STRAFE_RIGHT": (0.0, -strafe, 0.0),
            "ROTATE_LEFT": (0.0, 0.0, angular),
            "ROTATE_RIGHT": (0.0, 0.0, -angular),
            "FORWARD_LEFT": (forward * diagonal, strafe * diagonal, 0.0),
            "FORWARD_RIGHT": (forward * diagonal, -strafe * diagonal, 0.0),
            "BACKWARD_LEFT": (-forward * diagonal, strafe * diagonal, 0.0),
            "BACKWARD_RIGHT": (-forward * diagonal, -strafe * diagonal, 0.0),
        }
        vx, vy, wz = motions.get(motion, (0.0, 0.0, 0.0))
        return {"linear_x": vx, "linear_y": vy, "angular_z": wz}

    def _handle_rosbridge_message(self, raw_message: Any) -> None:
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8", errors="replace")
        payload = json.loads(raw_message)
        if payload.get("op") != "publish":
            return
        topic = payload.get("topic")
        message = payload.get("msg") or {}
        if topic == "/map":
            self._handle_map(message)
            return
        if topic == "/cubey/pose":
            self._handle_pose(message)
            return
        if topic == "/cubey/trajectory":
            self._handle_trajectory(message)
            return
        if topic == "/cubey/exploration/goal":
            self._handle_frontier_goal(message)
            return
        if topic == "/cubey/exploration/status":
            self._handle_exploration_status(message)
            return
        if topic != self._COMMAND_TOPIC:
            return
        linear = message.get("linear") or {}
        angular = message.get("angular") or {}
        command = {
            "linear_x": float(linear.get("x", 0.0)),
            "linear_y": float(linear.get("y", 0.0)),
            "angular_z": float(angular.get("z", 0.0)),
        }
        self._last_command = command
        self._last_command_received_at = time.monotonic()
        self._watchdog_stopped = False
        if self.command_output_enabled:
            self._apply_velocity_command(command)

    @staticmethod
    def _quaternion_yaw(orientation: Dict[str, Any]) -> float:
        x = float(orientation.get("x", 0.0))
        y = float(orientation.get("y", 0.0))
        z = float(orientation.get("z", 0.0))
        w = float(orientation.get("w", 1.0))
        return math.atan2(
            2.0 * (w * z + x * y),
            1.0 - 2.0 * (y * y + z * z),
        )

    @staticmethod
    def _ros_to_ui(x_m: float, y_m: float) -> tuple[float, float]:
        return -y_m, x_m

    def _handle_pose(self, message: Dict[str, Any]) -> None:
        pose = message.get("pose") or {}
        position = pose.get("position") or {}
        ros_x = float(position.get("x", 0.0))
        ros_y = float(position.get("y", 0.0))
        ui_x, ui_y = self._ros_to_ui(ros_x, ros_y)
        yaw = self._quaternion_yaw(pose.get("orientation") or {})
        with self._state_lock:
            self._pose = {
                "x_m": round(ui_x, 3),
                "y_m": round(ui_y, 3),
                "theta_deg": round((-math.degrees(yaw)) % 360.0, 1),
                "timestamp": time.time(),
            }

    def _handle_map(self, message: Dict[str, Any]) -> None:
        info = message.get("info") or {}
        width = int(info.get("width", 0))
        height = int(info.get("height", 0))
        resolution = float(info.get("resolution", 0.0))
        data = list(message.get("data") or [])
        if width <= 0 or height <= 0 or len(data) != width * height:
            return
        origin = (info.get("origin") or {}).get("position") or {}
        origin_x = float(origin.get("x", 0.0))
        origin_y = float(origin.get("y", 0.0))

        # ROS map axes are +X forward, +Y left. The existing Cubey canvas uses
        # +Y forward, +X right, so rotate the raster into that fixed world frame.
        ui_width = height
        ui_height = width
        rotated = bytearray(ui_width * ui_height)
        explored = 0
        for ros_y in range(height):
            source_row = ros_y * width
            ui_x = height - 1 - ros_y
            for ros_x in range(width):
                value = int(data[source_row + ros_x])
                ui_y = ros_x
                rotated[ui_y * ui_width + ui_x] = value & 0xFF
                if value >= 0:
                    explored += 1
        received_at = time.monotonic()
        snapshot = {
            "width": ui_width,
            "height": ui_height,
            "resolution_cm": resolution * 100.0,
            "origin_x_m": -(origin_y + height * resolution),
            "origin_y_m": origin_x,
            "total_explored_cells": explored,
            "received_at": received_at,
            "timestamp": time.time(),
        }
        with self._state_lock:
            self._map_snapshot = snapshot
            self._compressed_grid = zlib.compress(bytes(rotated), level=1)

    def _handle_trajectory(self, message: Dict[str, Any]) -> None:
        converted = []
        for stamped_pose in message.get("poses") or []:
            position = ((stamped_pose.get("pose") or {}).get("position") or {})
            ui_x, ui_y = self._ros_to_ui(
                float(position.get("x", 0.0)),
                float(position.get("y", 0.0)),
            )
            converted.append((round(ui_x, 3), round(ui_y, 3)))
        with self._state_lock:
            self._trajectory = converted[-3000:]

    def _handle_frontier_goal(self, message: Dict[str, Any]) -> None:
        position = ((message.get("pose") or {}).get("position") or {})
        target = self._ros_to_ui(
            float(position.get("x", 0.0)),
            float(position.get("y", 0.0)),
        )
        with self._state_lock:
            self._target_frontier = target

    def _handle_exploration_status(self, message: Dict[str, Any]) -> None:
        try:
            status = json.loads(str(message.get("data", "{}")))
        except (TypeError, ValueError):
            return
        if isinstance(status, dict):
            with self._state_lock:
                self._exploration_status = status

    def _update_laser_hits(self, scan: LidarScanData) -> None:
        with self._state_lock:
            pose = dict(self._pose)
        if not pose["timestamp"]:
            return
        theta = math.radians(pose["theta_deg"])
        sin_theta = math.sin(theta)
        cos_theta = math.cos(theta)
        hits = []
        for point in scan.points:
            if point.quality <= 0 or point.distance_mm <= 0.0:
                continue
            distance_m = point.distance_mm / 1000.0
            angle = math.radians(point.angle_deg)
            local_x = math.sin(angle) * distance_m
            local_y = math.cos(angle) * distance_m
            world_x = pose["x_m"] + local_x * cos_theta + local_y * sin_theta
            world_y = pose["y_m"] - local_x * sin_theta + local_y * cos_theta
            hits.append((round(world_x, 3), round(world_y, 3)))
        with self._state_lock:
            self._laser_hits = hits

    def _apply_velocity_command(self, command: Dict[str, float]) -> None:
        axes = [
            command["linear_x"] / self.max_forward_mps,
            command["linear_y"] / self.max_strafe_mps,
            command["angular_z"] / self.max_angular_rps,
        ]
        peak = max(1.0, *(abs(axis) for axis in axes))
        normalized = [int(round(axis / peak * 1000.0)) for axis in axes]
        if max(abs(value) for value in normalized) < 40:
            self.wheels_service.stop()
            self._last_applied_twist = {
                "linear_x": 0.0,
                "linear_y": 0.0,
                "angular_z": 0.0,
            }
            return
        if self.wheels_service.send_twist_normalized(*normalized):
            self._last_applied_twist = dict(command)

    def _enforce_command_watchdog(self) -> None:
        if not self.command_output_enabled or self._watchdog_stopped:
            return
        if (
            time.monotonic() - self._last_command_received_at
            > self.command_timeout_s
        ):
            self._stop_motion_once()

    def _stop_motion_once(self) -> None:
        if self.command_output_enabled and not self._watchdog_stopped:
            self.wheels_service.stop()
        self._watchdog_stopped = True
        self._last_applied_twist = {
            "linear_x": 0.0,
            "linear_y": 0.0,
            "angular_z": 0.0,
        }


_SHARED_ROS_BRIDGE_SERVICE: Optional[RosBridgeService] = None


def get_ros_bridge_service() -> RosBridgeService:
    global _SHARED_ROS_BRIDGE_SERVICE
    if _SHARED_ROS_BRIDGE_SERVICE is None:
        from src.config import config

        _SHARED_ROS_BRIDGE_SERVICE = RosBridgeService(
            url=config.rosbridge_url,
            command_output_enabled=config.ros2_command_output_enabled,
            max_forward_mps=config.ros2_max_forward_mps,
            max_strafe_mps=config.ros2_max_strafe_mps,
            max_angular_rps=config.ros2_max_angular_rps,
            command_timeout_s=config.ros2_command_timeout_s,
        )
    return _SHARED_ROS_BRIDGE_SERVICE
