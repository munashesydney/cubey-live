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
        gentle mecanum drive maneuvers to map the environment without bumping.
        """
        wheels_svc = get_wheels_service()
        lidar_svc = get_lidar_service()

        if not wheels_svc.is_connected:
            wheels_svc.connect()

        # Set low, gentle speed for smooth mapping & low wheel slippage
        AUTONOMOUS_SPEED = 110
        wheels_svc.set_speed(AUTONOMOUS_SPEED)

        SAFE_STOP_DIST_MM = 220   # Stop threshold (22 cm)
        SLOW_WARN_DIST_MM = 340   # Approaching obstacle threshold (34 cm)

        consecutive_turns = 0
        last_turn_dir = "rotateRight"

        self._emit_log(f"🤖 Autonomous Exploration active at speed {AUTONOMOUS_SPEED}.")
        logger.info("Autonomous Exploration loop started at speed %d", AUTONOMOUS_SPEED)

        while self._is_exploring:
            try:
                scan = lidar_svc.latest_scan
                front = scan.min_front_dist_mm if scan.min_front_dist_mm > 0 else 9999
                left = scan.min_left_dist_mm if scan.min_left_dist_mm > 0 else 9999
                right = scan.min_right_dist_mm if scan.min_right_dist_mm > 0 else 9999
                back = scan.min_back_dist_mm if scan.min_back_dist_mm > 0 else 9999

                action = "forward"

                # 1. Clear forward path
                if front > SLOW_WARN_DIST_MM:
                    action = "forward"
                    consecutive_turns = 0
                    wheels_svc.move("forward")
                    time.sleep(0.24)

                # 2. Approaching obstacle — nudge slightly toward clearest side
                elif front > SAFE_STOP_DIST_MM:
                    if left > right:
                        action = "forwardLeft"
                    else:
                        action = "forwardRight"
                    consecutive_turns = 0
                    wheels_svc.move(action)
                    time.sleep(0.20)

                # 3. Obstacle detected in front! Stop and evade intelligently
                else:
                    wheels_svc.stop()
                    time.sleep(0.08)
                    consecutive_turns += 1

                    # If stuck in a corner / dead-end, back up first
                    if consecutive_turns >= 3 and back > SAFE_STOP_DIST_MM:
                        action = "backward"
                        self._emit_log(f"Corner detected (turns={consecutive_turns}): backing up...")
                        wheels_svc.move("backward")
                        time.sleep(0.25)
                        wheels_svc.stop()
                        time.sleep(0.05)
                        consecutive_turns = 0

                    # Prefer strafing sideways if space allows (mecanum omni advantage!)
                    if left > 280 and left >= right:
                        action = "strafeLeft"
                        wheels_svc.move("strafeLeft")
                        time.sleep(0.25)
                    elif right > 280:
                        action = "strafeRight"
                        wheels_svc.move("strafeRight")
                        time.sleep(0.25)
                    elif left > right:
                        action = "rotateLeft"
                        last_turn_dir = "rotateLeft"
                        wheels_svc.move("rotateLeft")
                        time.sleep(0.18)
                    else:
                        action = "rotateRight"
                        last_turn_dir = "rotateRight"
                        wheels_svc.move("rotateRight")
                        time.sleep(0.18)

                # Brief settling pause between pulses for crisp LiDAR scan integration
                wheels_svc.stop()
                time.sleep(0.08)

                # Log decision
                logger.info(
                    "🤖 [AutoNav] F: %d mm, L: %d mm, R: %d mm, B: %d mm -> Action: %s (turns=%d)",
                    front, left, right, back, action, consecutive_turns
                )

            except Exception as e:
                logger.error("Error in autonomous navigation loop: %s", e)
                time.sleep(0.2)

        try:
            wheels_svc.stop()
            wheels_svc.set_speed(180)  # restore teleop speed
        except Exception:
            pass

    def _waypoint_navigation_loop(self, goal: NavGoal):
        """
        Maneuvers robot toward a target waypoint (X, Y) on the floorplan.
        """
        wheels_svc = get_wheels_service()
        mapping_svc = get_mapping_service()
        lidar_svc = get_lidar_service()

        if not wheels_svc.is_connected:
            wheels_svc.connect()

        WAYPOINT_SPEED = 115
        wheels_svc.set_speed(WAYPOINT_SPEED)

        GOAL_TOLERANCE_M = 0.20
        OBSTACLE_AVOID_MM = 220

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
                    time.sleep(0.08)
                    if scan.min_left_dist_mm > scan.min_right_dist_mm:
                        wheels_svc.move("strafeLeft")
                    else:
                        wheels_svc.move("strafeRight")
                    time.sleep(0.22)
                    wheels_svc.stop()
                    continue

                # Align heading or drive forward
                if abs(heading_err) > 25:
                    if heading_err > 0:
                        wheels_svc.move("rotateRight")
                    else:
                        wheels_svc.move("rotateLeft")
                    time.sleep(0.16)
                else:
                    wheels_svc.move("forward")
                    time.sleep(0.22)

                wheels_svc.stop()
                time.sleep(0.06)

            except Exception as e:
                logger.error("Error in waypoint navigation loop: %s", e)
                time.sleep(0.2)

        try:
            wheels_svc.stop()
            wheels_svc.set_speed(180)
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
