"""
Cubey Navigation Service — Autonomous Mapping, Obstacle Avoidance & Nav2 Interface.

Coordinates autonomous exploration (smart obstacle-avoiding wandering), target
waypoint navigation, and teleoperation modes. Bridges between Gemini Live / Web
interface and robot motion.
"""

import json
import math
import logging
import os
import random
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

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

    def _is_ros2_explorer_active(self) -> bool:
        """Check if native ROS 2 frontier explorer node is publishing heartbeats."""
        status_file = "/tmp/cubey_exploration_status.json"
        if not os.path.exists(status_file):
            return False
        try:
            with open(status_file, "r") as f:
                data = json.load(f)
            return (time.time() - float(data.get("timestamp", 0))) < 4.0
        except Exception:
            return False

    def _trigger_ros2_exploration(self, start: bool = True) -> bool:
        """Trigger ROS 2 exploration start/stop service."""
        srv = "/cubey/start_exploration" if start else "/cubey/stop_exploration"
        pixi_bin = os.path.expanduser("~/.pixi/bin/pixi")
        ros2_dir = "/home/cubey/Desktop/cubey-live/ros2"
        if os.path.exists(pixi_bin) and os.path.exists(ros2_dir):
            cmd = [pixi_bin, "run", "--manifest-path", f"{ros2_dir}/pixi.toml", "ros2", "service", "call", srv, "std_srvs/srv/Trigger"]
        else:
            cmd = ["ros2", "service", "call", srv, "std_srvs/srv/Trigger"]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5)
            return res.returncode == 0 and "success=True" in res.stdout
        except Exception as e:
            logger.debug("Could not call ROS 2 service %s: %s", srv, e)
            return False

    def _monitor_ros2_exploration(self):
        """Monitors /tmp/cubey_exploration_status.json while ROS 2 is autonomously exploring."""
        mapping_svc = get_mapping_service()
        status_file = "/tmp/cubey_exploration_status.json"
        last_state = "IDLE"

        while self._is_exploring:
            try:
                if os.path.exists(status_file):
                    with open(status_file, "r") as f:
                        data = json.load(f)

                    st = data.get("state", "EXPLORING")
                    if st != last_state:
                        last_state = st
                        self._emit_log(f"🤖 [Nav2] Exploration State: {st}")

                    with self._lock:
                        self.telemetry.state = st if st != "COMPLETED" else "IDLE"
                        self.telemetry.distance_remaining_m = float(data.get("distance_remaining_m", 0.0))
                        gx = data.get("goal_x")
                        gy = data.get("goal_y")
                        if gx is not None and gy is not None:
                            self.telemetry.current_goal = NavGoal(x_m=gx, y_m=gy)

                    self._emit_telemetry()

                    if st == "COMPLETED":
                        self._emit_log("🎉 Nav2 room exploration and return-to-dock complete!")
                        mapping_svc.pause_mapping()
                        with self._lock:
                            self._is_exploring = False
                            self.telemetry.state = "IDLE"
                            self.telemetry.mode = "manual"
                            self.telemetry.current_goal = None
                        self._emit_telemetry()
                        break
            except Exception as e:
                logger.debug("Error reading ROS 2 exploration status: %s", e)

            time.sleep(0.5)

    def start_exploration(self) -> bool:
        """
        Trigger autonomous room mapping with real-time reactive obstacle avoidance
        and auto-stop (delegating to ROS 2 Nav2 if active, or embedded frontier exploration).
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

        # Check if native ROS 2 Nav2 frontier supervisor is running
        if self._is_ros2_explorer_active() and self._trigger_ros2_exploration(start=True):
            self._worker_thread = threading.Thread(
                target=self._monitor_ros2_exploration,
                daemon=True,
                name="CubeyROS2NavMonitor"
            )
            self._worker_thread.start()
            self._emit_log("🤖 Activated Native ROS 2 Nav2 Frontier Exploration with Auto-Stop.")
            self._emit_telemetry()
            return True

        # Standalone embedded frontier exploration loop
        self._worker_thread = threading.Thread(
            target=self._autonomous_exploration_loop,
            daemon=True,
            name="CubeyAutonomousNavWorker"
        )
        self._worker_thread.start()

        self._emit_log("Started Autonomous Frontier Exploration & Obstacle Avoidance.")
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
        # Signal ROS 2 explorer to halt if active
        if self._is_ros2_explorer_active():
            self._trigger_ros2_exploration(start=False)

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
        Autonomous Vector Potential Field navigation loop.
        Computes continuous attractive (forward exploration) and repulsive (obstacle avoidance)
        force vectors directly from 360° LiDAR pointclouds, with SLAM-based stall recovery.
        """
        wheels_svc = get_wheels_service()
        lidar_svc = get_lidar_service()
        mapping_svc = get_mapping_service()

        if not wheels_svc.is_connected:
            wheels_svc.connect()

        AUTONOMOUS_SPEED = 110
        wheels_svc.set_speed(AUTONOMOUS_SPEED)

        # SLAM Stall Detection tracking
        last_tracked_x = mapping_svc.pose.x_m
        last_tracked_y = mapping_svc.pose.y_m
        last_tracked_th = mapping_svc.pose.theta_deg
        last_stall_check_time = time.time()
        last_explored_cells = 0
        last_cell_change_time = time.time()
        zero_frontier_cycles = 0

        self._emit_log(f"🤖 Vector Potential Field Exploration active (speed {AUTONOMOUS_SPEED}).")
        logger.info("Vector Potential Field Exploration loop started at speed %d", AUTONOMOUS_SPEED)

        SAFETY_RADIUS_M = 0.55  # 55cm repulsive boundary
        CRITICAL_STOP_M = 0.22  # 22cm emergency stop distance

        while self._is_exploring:
            try:
                scan = lidar_svc.latest_scan
                points = scan.points if scan else []

                # --- 1. Vector Potential Field Calculation ---
                # Repulsive force vector (pushing away from nearby obstacles)
                repulse_x = 0.0  # lateral (+ pushes right, - pushes left)
                repulse_y = 0.0  # longitudinal (+ pushes forward, - pushes backward)
                min_obstacle_dist = 999.0

                for pt in points:
                    dist_m = pt.distance_mm / 1000.0
                    if dist_m < 0.15 or dist_m > SAFETY_RADIUS_M or pt.quality <= 5:
                        continue

                    if dist_m < min_obstacle_dist:
                        min_obstacle_dist = dist_m

                    angle_rad = math.radians(pt.angle_deg)
                    # Sensor local frame: 0° fwd, 90° right, 180° back, 270° left
                    px = dist_m * math.sin(angle_rad)
                    py = dist_m * math.cos(angle_rad) - 0.035  # LiDAR offset

                    # Inverse square repulsive force
                    force = (1.0 / max(dist_m, 0.18) - 1.0 / SAFETY_RADIUS_M) ** 2
                    repulse_x -= (px / dist_m) * force
                    repulse_y -= (py / dist_m) * force

                # Attractive forward exploration bias
                FORWARD_ATTRACTIVE_FORCE = 2.2
                target_vy = FORWARD_ATTRACTIVE_FORCE + repulse_y
                target_vx = repulse_x

                # --- 2. Action Selection based on Resultant Force Vector ---
                action = "forward"
                duration = 0.22

                if min_obstacle_dist < CRITICAL_STOP_M or target_vy < -0.2:
                    # Trapped or head-on obstacle: stop and evade laterally/rotationally
                    wheels_svc.stop()
                    time.sleep(0.06)

                    if abs(target_vx) > 0.8:
                        action = "strafeRight" if target_vx > 0 else "strafeLeft"
                        duration = 0.24
                    else:
                        action = "rotateRight" if target_vx >= 0 else "rotateLeft"
                        duration = 0.20
                    wheels_svc.move(action)
                    time.sleep(duration)
                elif abs(target_vx) > 1.2:
                    # Strong side repulsion: steer diagonally
                    action = "forwardRight" if target_vx > 0 else "forwardLeft"
                    wheels_svc.move(action)
                    time.sleep(0.20)
                else:
                    # Clear path ahead: glide forward
                    action = "forward"
                    wheels_svc.move("forward")
                    time.sleep(0.22)

                wheels_svc.stop()
                time.sleep(0.06)

                # --- 3. SLAM-Based Stall & Slip Detection ---
                now = time.time()
                if now - last_stall_check_time >= 0.9:
                    curr_pose = mapping_svc.pose
                    dist_moved = math.hypot(curr_pose.x_m - last_tracked_x, curr_pose.y_m - last_tracked_y)
                    th_moved = abs(curr_pose.theta_deg - last_tracked_th)

                    if dist_moved < 0.02 and th_moved < 4.0:
                        # STALL DETECTED: Motors commanding motion but SLAM pose stationary
                        logger.warning("🤖 [AutoNav] Stall detected! Executing Committed 3-Phase Escape Sequence...")
                        self._emit_log("⚠️ Obstacle stall: executing 3-phase escape maneuver...")

                        # Phase 1: Full reverse back (20-25 cm)
                        wheels_svc.stop()
                        time.sleep(0.05)
                        wheels_svc.move("backward")
                        time.sleep(0.42)
                        wheels_svc.stop()
                        time.sleep(0.06)

                        # Phase 2: Rotate 90°-120° away from the obstruction
                        escape_turn = "rotateRight" if target_vx >= 0 else "rotateLeft"
                        wheels_svc.move(escape_turn)
                        time.sleep(0.45)
                        wheels_svc.stop()
                        time.sleep(0.06)

                        # Phase 3: Drive forward into clear open space to exit furniture zone
                        wheels_svc.move("forward")
                        time.sleep(0.70)
                        wheels_svc.stop()
                        time.sleep(0.06)

                        # Re-sync tracking pose after escape
                        new_pose = mapping_svc.pose
                        last_tracked_x = new_pose.x_m
                        last_tracked_y = new_pose.y_m
                        last_tracked_th = new_pose.theta_deg
                        last_stall_check_time = time.time()
                        continue
                    else:
                        last_tracked_x = curr_pose.x_m
                        last_tracked_y = curr_pose.y_m
                        last_tracked_th = curr_pose.theta_deg
                        last_stall_check_time = now


                # --- 4. Frontier-Based Auto-Stop Protocol ---
                grid = mapping_svc._grid
                free = (grid >= 0) & (grid < 25)
                unknown = (grid == -1)
                curr_explored = int(np.count_nonzero(grid != -1))

                if np.any(free) and np.any(unknown):
                    has_unknown = np.zeros_like(free, dtype=bool)
                    has_unknown[:-1, :] |= unknown[1:, :]
                    has_unknown[1:, :]  |= unknown[:-1, :]
                    has_unknown[:, :-1] |= unknown[:, 1:]
                    has_unknown[:, 1:]  |= unknown[:, :-1]
                    frontiers = free & has_unknown
                    frontier_count = int(np.count_nonzero(frontiers))
                else:
                    frontier_count = 0

                # Detect when all accessible frontiers in room are mapped
                if frontier_count == 0 and curr_explored > 120:
                    zero_frontier_cycles += 1
                    if zero_frontier_cycles >= 3:
                        self._emit_log(f"🎉 Floorplan exploration complete! Mapped {curr_explored} cells.")
                        logger.info("Exploration complete — initiating auto-stop & return-to-dock.")

                        # Phase 2: Return to Dock Origin (0.0, 0.0)
                        self._emit_log("🤖 Returning to dock origin (0.0, 0.0)...")
                        with self._lock:
                            self.telemetry.state = "RETURNING_TO_DOCK"
                        self._emit_telemetry()

                        self._execute_return_to_origin(wheels_svc, mapping_svc, lidar_svc)

                        # Phase 3: Finalize and save map to SQLite
                        mapping_svc.pause_mapping()
                        try:
                            map_name = f"Auto-Floorplan {time.strftime('%Y-%m-%d %H:%M')}"
                            mapping_svc.save_current_map(name=map_name)
                            self._emit_log(f"💾 Map '{map_name}' successfully saved to database.")
                        except Exception as e:
                            logger.warning("Could not auto-save map: %s", e)

                        # Phase 4: State finalization
                        with self._lock:
                            self._is_exploring = False
                            self.telemetry.state = "IDLE"
                            self.telemetry.mode = "manual"
                            self.telemetry.current_goal = None
                        self._emit_log("✅ Room mapping and auto-stop complete!")
                        self._emit_telemetry()
                        break
                else:
                    zero_frontier_cycles = 0

                logger.info(
                    "🤖 [AutoNav-PF] Force=(vx=%.2f, vy=%.2f) MinObs=%.2fm Frontiers=%d -> Action: %s",
                    target_vx, target_vy, min_obstacle_dist, frontier_count, action
                )

            except Exception as e:
                logger.error("Error in potential field exploration loop: %s", e)
                time.sleep(0.2)

        try:
            wheels_svc.stop()
            wheels_svc.set_speed(180)  # restore teleop speed
        except Exception:
            pass

        with self._lock:
            if self.telemetry.state == "EXPLORING":
                self.telemetry.state = "IDLE"
            self._is_exploring = False
        self._emit_telemetry()

    def _execute_return_to_origin(self, wheels_svc, mapping_svc, lidar_svc):
        """Maneuver robot back to starting origin (0, 0) upon exploration completion."""
        DOCK_TOLERANCE_M = 0.25
        wheels_svc.set_speed(110)
        start_t = time.time()

        while time.time() - start_t < 40.0:
            pose = mapping_svc.pose
            dist = math.hypot(pose.x_m, pose.y_m)
            with self._lock:
                self.telemetry.distance_remaining_m = dist
            self._emit_telemetry()

            if dist <= DOCK_TOLERANCE_M:
                break

            # Angle toward (0, 0)
            dx = 0.0 - pose.x_m
            dy = 0.0 - pose.y_m
            target_heading = math.degrees(math.atan2(dx, dy))
            heading_err = target_heading - pose.theta_deg
            while heading_err > 180:
                heading_err -= 360
            while heading_err < -180:
                heading_err += 360

            # Front safety clearance
            scan = lidar_svc.latest_scan
            front = scan.min_front_dist_mm if scan and scan.min_front_dist_mm > 0 else 9999
            if front < 250:
                wheels_svc.stop()
                time.sleep(0.06)
                if scan.min_left_dist_mm > scan.min_right_dist_mm:
                    wheels_svc.move("strafeLeft")
                else:
                    wheels_svc.move("strafeRight")
                time.sleep(0.22)
                wheels_svc.stop()
                continue

            if abs(heading_err) > 30:
                wheels_svc.move("rotateRight" if heading_err > 0 else "rotateLeft")
                time.sleep(0.16)
            else:
                wheels_svc.move("forward")
                time.sleep(0.22)

            wheels_svc.stop()
            time.sleep(0.06)

        wheels_svc.stop()


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
