"""
Cubey Navigation Service — Autonomous Mapping, Obstacle Avoidance & Nav2 Interface.

Coordinates autonomous exploration (smart obstacle-avoiding wandering), target
waypoint navigation, and teleoperation modes. Bridges between Gemini Live / Web
interface and robot motion.
"""

import math
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.services.lidar_service import get_lidar_service
from src.services.wheels_service import get_wheels_service
from src.services.mapping_service import get_mapping_service

logger = logging.getLogger(__name__)


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

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.telemetry.state in ("NAVIGATING", "EXPLORING")

    @property
    def is_autonomous(self) -> bool:
        with self._lock:
            return self.telemetry.mode == "autonomous" and self._is_exploring

    def start_manual_mapping(self) -> bool:
        """Switch to manual teleop mapping mode."""
        with self._lock:
            self._is_exploring = False
            self._is_navigating_goal = False
            self.telemetry.state = "MANUAL"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None

        mapping_svc = get_mapping_service()
        mapping_svc.start_mapping()

        self._emit_log("Manual Mapping mode enabled. Drive using keyboard/joystick.")
        self._emit_telemetry()
        return True

    def start_exploration(self) -> bool:
        """
        Trigger autonomous room mapping with real-time reactive obstacle avoidance.
        """
        self.stop_navigation()

        with self._lock:
            self._is_exploring = True
            self._is_navigating_goal = False
            self.telemetry.state = "EXPLORING"
            self.telemetry.mode = "autonomous"

        # Ensure mapping engine and LiDAR are active
        mapping_svc = get_mapping_service()
        mapping_svc.start_mapping()

        self._worker_thread = threading.Thread(
            target=self._autonomous_exploration_loop,
            daemon=True,
            name="CubeyAutonomousNavWorker"
        )
        self._worker_thread.start()

        self._emit_log("Started Autonomous Mapping & Obstacle Avoidance Exploration.")
        self._emit_telemetry()
        return True

    def navigate_to(self, x_m: float, y_m: float, theta_deg: float = 0.0) -> bool:
        """
        Send a 2D navigation waypoint target.
        """
        self.stop_navigation()

        goal = NavGoal(x_m=x_m, y_m=y_m, theta_deg=theta_deg)
        with self._lock:
            self._is_navigating_goal = True
            self._is_exploring = False
            self.telemetry.state = "NAVIGATING"
            self.telemetry.mode = "autonomous"
            self.telemetry.current_goal = goal

        mapping_svc = get_mapping_service()
        mapping_svc.start_mapping()

        self._worker_thread = threading.Thread(
            target=self._waypoint_navigation_loop,
            args=(goal,),
            daemon=True,
            name="CubeyWaypointNavWorker"
        )
        self._worker_thread.start()

        self._emit_log(f"Navigating to waypoint ({x_m:.2f}m, {y_m:.2f}m)...")
        self._emit_telemetry()
        return True

    def stop_navigation(self) -> bool:
        """
        Halt all autonomous motion and set state to IDLE.
        """
        with self._lock:
            self._is_exploring = False
            self._is_navigating_goal = False
            self.telemetry.state = "IDLE"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None
            self.telemetry.distance_remaining_m = 0.0

        # Send immediate stop command to wheels
        try:
            wheels_svc = get_wheels_service()
            wheels_svc.stop()
        except Exception as e:
            logger.warning("Error stopping wheels: %s", e)

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=0.5)
        self._worker_thread = None

        self._emit_log("Autonomous navigation stopped.")
        self._emit_telemetry()
        return True

    # ------------------------------------------------------------------
    # Autonomous Reactive Navigation Routines
    # ------------------------------------------------------------------

    def _autonomous_exploration_loop(self):
        """
        Autonomous wandering loop combining LiDAR proximity telemetry and
        smooth mecanum drive maneuvers to map the environment without bumping.
        """
        wheels_svc = get_wheels_service()
        lidar_svc = get_lidar_service()

        OBSTACLE_THRESHOLD_MM = 380  # Stop & turn threshold
        CLEAR_THRESHOLD_MM = 550     # Clear forward path threshold

        while self._is_exploring:
            try:
                scan = lidar_svc.latest_scan
                front = scan.min_front_dist_mm if scan.min_front_dist_mm > 0 else 9999
                left = scan.min_left_dist_mm if scan.min_left_dist_mm > 0 else 9999
                right = scan.min_right_dist_mm if scan.min_right_dist_mm > 0 else 9999
                back = scan.min_back_dist_mm if scan.min_back_dist_mm > 0 else 9999

                # Check if path in front is clear
                if front > CLEAR_THRESHOLD_MM:
                    # Move forward smoothly
                    wheels_svc.send_command("forward")
                    time.sleep(0.35)
                elif front > OBSTACLE_THRESHOLD_MM:
                    # Approaching obstacle — nudge slightly toward clearest side
                    if left > right:
                        wheels_svc.send_command("forwardLeft")
                    else:
                        wheels_svc.send_command("forwardRight")
                    time.sleep(0.30)
                else:
                    # Obstacle detected in front! Stop and evade
                    wheels_svc.stop()
                    time.sleep(0.10)

                    # Turn or strafe toward the side with more open space
                    if left > right and left > OBSTACLE_THRESHOLD_MM:
                        self._emit_log("Obstacle ahead: rotating left to explore open space...")
                        wheels_svc.send_command("rotateLeft")
                        time.sleep(0.40)
                    elif right > OBSTACLE_THRESHOLD_MM:
                        self._emit_log("Obstacle ahead: rotating right to explore open space...")
                        wheels_svc.send_command("rotateRight")
                        time.sleep(0.40)
                    elif back > OBSTACLE_THRESHOLD_MM:
                        self._emit_log("Corner/dead-end detected: reversing out...")
                        wheels_svc.send_command("backward")
                        time.sleep(0.35)
                        wheels_svc.send_command("rotateRight")
                        time.sleep(0.45)
                    else:
                        # Full 180 spin if trapped in a tight space
                        wheels_svc.send_command("rotateRight")
                        time.sleep(0.60)

                    wheels_svc.stop()
                    time.sleep(0.10)

            except Exception as e:
                logger.error("Error in autonomous navigation loop: %s", e)
                time.sleep(0.2)

        try:
            wheels_svc.stop()
        except Exception:
            pass

    def _waypoint_navigation_loop(self, goal: NavGoal):
        """
        Maneuvers robot toward a target waypoint (X, Y) on the floorplan.
        """
        wheels_svc = get_wheels_service()
        mapping_svc = get_mapping_service()
        lidar_svc = get_lidar_service()

        GOAL_TOLERANCE_M = 0.20
        OBSTACLE_AVOID_MM = 350

        while self._is_navigating_goal:
            try:
                current_pose = mapping_svc.pose
                dx = goal.x_m - current_pose.x_m
                dy = goal.y_m - current_pose.y_m
                dist = math.hypot(dx, dy)

                with self._lock:
                    self.telemetry.distance_remaining_m = dist

                self._emit_telemetry()

                # Check if reached goal
                if dist <= GOAL_TOLERANCE_M:
                    self._emit_log(f"🎯 Target waypoint reached ({goal.x_m:.2f}m, {goal.y_m:.2f}m)!")
                    with self._lock:
                        self.telemetry.state = "REACHED"
                        self._is_navigating_goal = False
                    wheels_svc.stop()
                    break

                # Target angle relative to current robot heading
                target_heading_deg = math.degrees(math.atan2(dx, dy))
                heading_err = target_heading_deg - current_pose.theta_deg
                while heading_err > 180:
                    heading_err -= 360
                while heading_err < -180:
                    heading_err += 360

                # Check front proximity for safety
                scan = lidar_svc.latest_scan
                front = scan.min_front_dist_mm if scan.min_front_dist_mm > 0 else 9999

                if front < OBSTACLE_AVOID_MM:
                    # Temporary avoidance nudge
                    self._emit_log("Waypoint path blocked by obstacle: evading...")
                    wheels_svc.stop()
                    time.sleep(0.1)
                    if scan.min_left_dist_mm > scan.min_right_dist_mm:
                        wheels_svc.send_command("strafeLeft")
                    else:
                        wheels_svc.send_command("strafeRight")
                    time.sleep(0.35)
                    continue

                # Align heading or drive forward
                if abs(heading_err) > 25:
                    if heading_err > 0:
                        wheels_svc.send_command("rotateRight")
                    else:
                        wheels_svc.send_command("rotateLeft")
                    time.sleep(0.20)
                else:
                    wheels_svc.send_command("forward")
                    time.sleep(0.30)

                wheels_svc.stop()
                time.sleep(0.05)

            except Exception as e:
                logger.error("Error in waypoint navigation loop: %s", e)
                time.sleep(0.2)

        try:
            wheels_svc.stop()
        except Exception:
            pass

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
