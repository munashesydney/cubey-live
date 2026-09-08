"""
Cubey Navigation Service — Autonomous Mapping, Obstacle Avoidance & Nav2 Interface.

Coordinates autonomous exploration (smart obstacle-avoiding wandering), target
waypoint navigation, and teleoperation modes. Bridges between Gemini Live / Web
interface and robot motion.
"""

import json
import logging
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from src.services.wheels_service import get_wheels_service
from src.services.mapping_service import get_mapping_service


logger = logging.getLogger(__name__)
ROS2_STATUS_FILE = "/tmp/cubey_exploration_status.json"
ROS2_COMMAND_ADDRESS = ("127.0.0.1", 9877)
ROS2_HEARTBEAT_MAX_AGE_S = 3.0


@dataclass
class NavGoal:
    """A target 2D waypoint in world/map coordinates."""
    x_m: float
    y_m: float
    theta_deg: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class NavTelemetry:
    """Real-time navigation state snapshot."""
    state: str = "IDLE"  # IDLE, MANUAL, EXPLORING, NAVIGATING, REACHED, BLOCKED, STOPPED
    mode: str = "manual"  # manual, autonomous
    current_goal: Optional[NavGoal] = None
    distance_remaining_m: float = 0.0
    estimated_time_remaining_s: float = 0.0
    timestamp: float = field(default_factory=time.time)
    failure_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "mode": self.mode,
            "current_goal": {
                "x_m": round(self.current_goal.x_m, 2),
                "y_m": round(self.current_goal.y_m, 2),
                "theta_deg": round(self.current_goal.theta_deg, 1),
            } if self.current_goal else None,
            "distance_remaining_m": round(self.distance_remaining_m, 2),
            "estimated_time_remaining_s": round(self.estimated_time_remaining_s, 1),
            "timestamp": self.timestamp,
            "failure_reason": self.failure_reason,
        }


class CubeyNavService:
    """
    Coordinates high-level navigation goals and autonomous exploration.
    Bridges between Gemini Live / Web interface and robot motion.
    """

    def __init__(
        self,
        on_telemetry: Optional[Callable[[NavTelemetry], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.on_telemetry = on_telemetry
        self.on_log = on_log

        self._lock = threading.Lock()
        self.telemetry = NavTelemetry()
        self._is_exploring = False
        self._is_navigating_goal = False
        self._worker_thread: Optional[threading.Thread] = None
        self._pending_mission_id = None
        self._request_generation = 0
        self.last_reset_error = ""

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.telemetry.state in ("PREPARING", "RESETTING", "NAVIGATING", "EXPLORING", "RETURNING_TO_DOCK", "RECOVERING_STUCK", "FINALIZING_MAP")

    @property
    def is_autonomous(self) -> bool:
        with self._lock:
            return self.telemetry.mode == "autonomous" and self._is_exploring

    def start_manual_mapping(self) -> bool:
        """Switch to manual teleop while SLAM Toolbox continues building /map."""
        if not self.is_ros2_ready():
            self._emit_log("Nav2/SLAM is not ready. Manual mapping was not started.")
            return False

        with self._lock:
            self._is_exploring = False
            self._is_navigating_goal = False
            self.telemetry.state = "MANUAL"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None

        mapping_svc = get_mapping_service()
        mapping_svc.start_mapping(external_pose=True)

        self._emit_log("Manual Mapping mode enabled. Drive using keyboard/joystick.")
        self._emit_telemetry()
        return True

    def _read_ros2_status(self) -> Optional[Dict[str, Any]]:
        try:
            with open(ROS2_STATUS_FILE, "r", encoding="utf-8") as status_stream:
                data = json.load(status_stream)
            if not isinstance(data, dict):
                return None
            return data
        except (OSError, ValueError, TypeError):
            return None

    def is_ros2_ready(self) -> bool:
        """Return true only while the native ROS 2 explorer heartbeat is fresh."""
        data = self._read_ros2_status()
        if not data:
            return False
        try:
            return 0 <= (time.time() - float(data.get("timestamp", 0.0))) <= ROS2_HEARTBEAT_MAX_AGE_S
        except (TypeError, ValueError):
            return False

    def _send_ros2_command(self, command: str, **payload: Any) -> bool:
        """Send one command to the native ROS 2 supervisor over loopback only."""
        try:
            message = {"command": command, **payload}
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as command_socket:
                command_socket.sendto(json.dumps(message).encode("utf-8"), ROS2_COMMAND_ADDRESS)
            return True
        except Exception as e:
            logger.warning("Could not send command to ROS 2 explorer: %s", e)
            return False

    def _wait_for_ros2_state(self, expected_states: set[str], sent_at: float, timeout_s: float = 5.0, mission_id=None) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            data = self._read_ros2_status()
            if data:
                try:
                    is_new = float(data.get("timestamp", 0.0)) >= sent_at
                except (TypeError, ValueError):
                    is_new = False
                if is_new and data.get("state") in expected_states and (mission_id is None or data.get("mission_id") == mission_id):
                    return True
                if is_new and data.get("state") == "ERROR" and data.get("mission_id") == mission_id:
                    return False
            time.sleep(0.1)
        return False

    def _monitor_ros2_exploration(self):
        """Monitors /tmp/cubey_exploration_status.json while ROS 2 Nav2 is autonomously exploring."""
        mapping_svc = get_mapping_service()
        last_state = "IDLE"
        last_healthy_heartbeat = time.time()
        monitored_mission = self._pending_mission_id

        while self._is_exploring and self._pending_mission_id == monitored_mission:
            try:
                data = self._read_ros2_status()
                if data and (monitored_mission is None or data.get("mission_id") == monitored_mission):
                    try:
                        heartbeat_age = time.time() - float(data.get("timestamp", 0.0))
                    except (TypeError, ValueError):
                        heartbeat_age = ROS2_HEARTBEAT_MAX_AGE_S + 1.0

                    if 0 <= heartbeat_age <= ROS2_HEARTBEAT_MAX_AGE_S:
                        last_healthy_heartbeat = time.time()
                    else:
                        if time.time()-last_healthy_heartbeat > ROS2_HEARTBEAT_MAX_AGE_S:
                            self.stop_navigation()
                            mapping_svc.pause_mapping()
                            self.telemetry.state = "ERROR"
                            self.telemetry.failure_reason = "Navigation heartbeat expired"
                            break
                        time.sleep(0.1)
                        continue

                    st = data.get("state", "EXPLORING")
                    if st != last_state:
                        last_state = st
                        self._emit_log(f"🤖 [Nav2] Exploration State: {st}")

                    completed = st in ("COMPLETED", "COMPLETED_AWAY_FROM_DOCK")
                    with self._lock:
                        self.telemetry.state = st
                        self.telemetry.failure_reason = data.get("failure_reason", "")
                        self.telemetry.distance_remaining_m = float(data.get("distance_remaining_m", 0.0))
                        gx = data.get("goal_x")
                        gy = data.get("goal_y")
                        if gx is not None and gy is not None:
                            self.telemetry.current_goal = NavGoal(x_m=gx, y_m=gy)

                    self._emit_telemetry()

                    if completed:
                        if st == "COMPLETED":
                            self._emit_log(
                                "🎉 Nav2 room exploration, return-to-dock, and map save complete!"
                            )
                        else:
                            self._emit_log(
                                "⚠️ Nav2 saved the map and stopped safely, but could not return to dock."
                            )
                        mapping_svc.pause_mapping()
                        with self._lock:
                            self._is_exploring = False
                            self.telemetry.state = st
                            self.telemetry.mode = "manual"
                            self.telemetry.current_goal = None
                        self._emit_telemetry()
                        break

                    if st == "ERROR":
                        mapping_svc.pause_mapping()
                        get_wheels_service().stop()
                        with self._lock:
                            self._is_exploring = False
                            self.telemetry.state = "ERROR"
                            self.telemetry.mode = "manual"
                            self.telemetry.current_goal = None
                        self._emit_log(
                            "Nav2 mapping could not be finalized; Cubey stopped safely. " + data.get("failure_reason", "")
                        )
                        self._emit_telemetry()
                        break

                if time.time() - last_healthy_heartbeat > ROS2_HEARTBEAT_MAX_AGE_S:
                    mapping_svc.pause_mapping()
                    with self._lock:
                        self._is_exploring = False
                        self.telemetry.state = "ERROR"
                        self.telemetry.mode = "manual"
                        self.telemetry.current_goal = None
                    get_wheels_service().stop()
                    self._emit_log("Nav2 heartbeat was lost. Autonomous mapping stopped safely.")
                    self._emit_telemetry()
                    break
            except Exception as e:
                logger.debug("Error reading ROS 2 exploration status: %s", e)

            time.sleep(0.4)

    def start_exploration(self) -> bool:
        """
        Trigger Native ROS 2 Nav2 Autonomous Frontier Exploration with Auto-Stop.
        """
        self.stop_navigation()
        request_generation = self._request_generation
        mission_id = str(uuid.uuid4())
        self._pending_mission_id = mission_id

        if not self.is_ros2_ready():
            self._emit_log("Nav2 is not ready. Autonomous mapping was not started.")
            return False

        mapping_svc = get_mapping_service()

        with self._lock:
            if request_generation != self._request_generation:
                return False
            mapping_svc.start_mapping(external_pose=True)
            self._is_exploring = True
            self._is_navigating_goal = False
            self.telemetry.state = "PREPARING"
            self.telemetry.failure_reason = ""
            self.telemetry.mode = "autonomous"
            sent_at = time.time()
            sent = self._send_ros2_command("start", mission_id=mission_id)

        accepted = sent and self._wait_for_ros2_state(
            {"PREPARING", "RESETTING", "EXPLORING"}, sent_at, mission_id=mission_id)
        if request_generation != self._request_generation:
            return False  # A concurrent Stop superseded this start.
        if not accepted:
            self._send_ros2_command("stop")
            get_wheels_service().stop()
            mapping_svc.pause_mapping()
            with self._lock:
                self._is_exploring = False
                self.telemetry.state = "ERROR"
                self.telemetry.mode = "manual"
            self._emit_log("Nav2 did not acknowledge autonomous mapping. Nothing was started.")
            self._emit_telemetry()
            return False

        self._worker_thread = threading.Thread(
            target=self._monitor_ros2_exploration,
            daemon=True,
            name="CubeyROS2NavMonitor"
        )
        self._worker_thread.start()

        self._emit_log("🤖 Activated Native ROS 2 Nav2 Autonomous Exploration with Auto-Stop.")
        self._emit_telemetry()
        return True

    def reset_mapping(self) -> bool:
        """Stop motion and reset both the real SLAM graph and legacy UI state."""
        self.stop_navigation()

        self.last_reset_error = ""
        if not self.is_ros2_ready():
            self.last_reset_error = "ROS navigation service is unavailable."
            self._emit_log("Nav2/SLAM is not ready. The map was not reset.")
            return False

        reset_id = str(uuid.uuid4())
        sent_at = time.time()
        if not self._send_ros2_command("reset", mission_id=reset_id) or not self._wait_for_ros2_state(
            {"IDLE"}, sent_at, timeout_s=27.0, mission_id=reset_id
        ):
            status = self._read_ros2_status() or {}
            self.last_reset_error = (status.get("failure_reason") if status.get("mission_id") == reset_id else None) or "Mapping reset timed out while waiting for healthy sensors and localization."
            self._send_ros2_command("stop")
            self._emit_log(self.last_reset_error)
            return False

        # Only clear the web map after this exact reset has completed in ROS.
        get_mapping_service().reset_map()
        with self._lock:
            self._is_exploring = False
            self._is_navigating_goal = False
            self.telemetry.state = "IDLE"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None
            self.telemetry.distance_remaining_m = 0.0

        self._emit_log("SLAM map and robot pose reset to a blank origin.")
        self._emit_telemetry()
        return True

    def navigate_to(self, x_m: float, y_m: float, theta_deg: float = 0.0) -> bool:
        """Send a waypoint to the real Nav2 NavigateToPose action client."""
        self.stop_navigation()

        if not self.is_ros2_ready():
            self._emit_log("Nav2 is not ready. Waypoint was not sent.")
            return False

        goal = NavGoal(x_m=x_m, y_m=y_m, theta_deg=theta_deg)
        with self._lock:
            self._is_navigating_goal = True
            self._is_exploring = False
            self.telemetry.state = "NAVIGATING"
            self.telemetry.mode = "autonomous"
            self.telemetry.current_goal = goal

        sent_at = time.time()
        if not self._send_ros2_command("navigate", x_m=x_m, y_m=y_m, theta_deg=theta_deg):
            with self._lock:
                self._is_navigating_goal = False
                self.telemetry.state = "ERROR"
            return False

        if not self._wait_for_ros2_state({"NAVIGATING", "REACHED"}, sent_at):
            with self._lock:
                self._is_navigating_goal = False
                self.telemetry.state = "ERROR"
            self._emit_log("Nav2 did not acknowledge the waypoint.")
            return False

        self._emit_log(f"Navigating to waypoint ({x_m:.2f}m, {y_m:.2f}m)...")
        self._emit_telemetry()
        return True

    def stop_navigation(self) -> bool:
        """Halt all autonomous motion and reset state."""
        with self._lock:
            self._request_generation += 1
            self._pending_mission_id = None
            self._is_exploring = False
            self._is_navigating_goal = False
            self.telemetry.state = "IDLE"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None
            self.telemetry.distance_remaining_m = 0.0

        # Signal ROS 2 explorer/Nav2 action client to halt.
        self._send_ros2_command("stop")

        # Send immediate stop command to wheels via bridge
        try:
            wheels_svc = get_wheels_service()
            wheels_svc.stop()
        except Exception as e:
            logger.warning("Error stopping wheels: %s", e)

        if self._worker_thread and self._worker_thread.is_alive() and threading.current_thread() != self._worker_thread:
            self._worker_thread.join(timeout=0.5)
        self._worker_thread = None

        self._emit_log("Autonomous navigation stopped.")
        self._emit_telemetry()
        return True


    def _emit_telemetry(self) -> None:
        if self.on_telemetry:
            try:
                self.on_telemetry(self.telemetry)
            except Exception as e:
                logger.warning("Error dispatching nav telemetry: %s", e)

    def _emit_log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
            except Exception as e:
                logger.warning("Error dispatching nav log: %s", e)


# Global Singleton
_SHARED_NAV_SERVICE: Optional[CubeyNavService] = None


def get_nav_service() -> CubeyNavService:
    global _SHARED_NAV_SERVICE
    if _SHARED_NAV_SERVICE is None:
        _SHARED_NAV_SERVICE = CubeyNavService()
    return _SHARED_NAV_SERVICE
