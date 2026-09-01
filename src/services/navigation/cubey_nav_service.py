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
import signal
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
        self._nav2_process: Optional[subprocess.Popen] = None

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
        Launch Native ROS 2 Nav2 Autonomous Frontier Exploration with Auto-Stop.
        Releases hardware serial ports in Python so Nav2 nodes take exclusive control.
        """
        self.stop_navigation()

        with self._lock:
            self._is_exploring = True
            self._is_navigating_goal = False
            self.telemetry.state = "EXPLORING"
            self.telemetry.mode = "autonomous"

        # Step 1: Release hardware serial ports in Python for ROS 2 nodes
        try:
            lidar_svc = get_lidar_service()
            wheels_svc = get_wheels_service()
            lidar_svc.disconnect()
            wheels_svc.disconnect()
        except Exception as e:
            logger.warning("Error disconnecting serial ports for Nav2: %s", e)

        time.sleep(0.3)

        # Clean up old status/map IPC files
        for f in ("/tmp/cubey_exploration_status.json", "/tmp/cubey_nav2_live_map.json"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

        # Step 2: Launch Native Nav2 bringup process in background
        pixi_bin = os.path.expanduser("~/.pixi/bin/pixi")
        ros2_dir = "/home/cubey/Desktop/cubey-live/ros2"
        if os.path.exists(pixi_bin) and os.path.exists(ros2_dir):
            cmd = [pixi_bin, "run", "--manifest-path", f"{ros2_dir}/pixi.toml", "launch-explore"]
        else:
            cmd = ["ros2", "launch", "launch/cubey_bringup.launch.py", "explore:=true"]

        try:
            self._nav2_process = subprocess.Popen(
                cmd,
                cwd=ros2_dir if os.path.exists(ros2_dir) else None,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None
            )
            logger.info("Launched Native Nav2 Exploration process (PID: %d)", self._nav2_process.pid)
            self._emit_log("🚀 Launched Native Nav2 Autonomous Exploration Stack.")
        except Exception as e:
            logger.error("Failed to launch Nav2 stack: %s", e)
            self._emit_log(f"❌ Failed to launch Nav2: {e}")
            self.stop_navigation()
            return False

        # Step 3: Monitor Nav2 lifecycle & Auto-Stop
        self._worker_thread = threading.Thread(
            target=self._monitor_nav2_exploration,
            daemon=True,
            name="CubeyNav2Supervisor"
        )
        self._worker_thread.start()

        self._emit_telemetry()
        return True

    def _monitor_nav2_exploration(self):
        """Monitors Native Nav2 process and /tmp/cubey_exploration_status.json."""
        status_file = "/tmp/cubey_exploration_status.json"
        last_state = "IDLE"

        while self._is_exploring:
            # Check if process terminated unexpectedly
            if self._nav2_process and self._nav2_process.poll() is not None:
                logger.warning("Nav2 process exited with code %d", self._nav2_process.returncode)
                break

            if os.path.exists(status_file):
                try:
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
                        self._emit_log("🎉 Nav2 room exploration and return-to-dock complete! Finalizing...")
                        time.sleep(2.0)  # Give time for map serialization to disk
                        break
                except Exception as e:
                    logger.debug("Error reading Nav2 exploration status: %s", e)

            time.sleep(0.5)

        # Exploration ended or complete: Clean shutdown
        self._shutdown_nav2_stack()

    def _shutdown_nav2_stack(self):
        """Cleanly terminate Nav2 process and reconnect Python serial drivers."""
        if self._nav2_process:
            pid = self._nav2_process.pid
            logger.info("Shutting down Nav2 process PID %d...", pid)
            try:
                if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(pid), signal.SIGINT)
                else:
                    self._nav2_process.send_signal(signal.SIGINT)
                self._nav2_process.wait(timeout=3.0)
            except Exception:
                try:
                    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    else:
                        self._nav2_process.kill()
                except Exception:
                    pass
            self._nav2_process = None

        # Clean up any lingering ros2 processes on the system
        try:
            subprocess.run(["pkill", "-9", "-f", "cubey_bringup|slam_toolbox|nav2_|cubey_cmd_vel|cubey_frontier"], capture_output=True, timeout=1.5)
        except Exception:
            pass

        time.sleep(0.5)

        # Reconnect serial ports for teleoperation and Gemini Live voice
        try:
            wheels_svc = get_wheels_service()
            lidar_svc = get_lidar_service()
            wheels_svc.connect()
            lidar_svc.connect()
        except Exception as e:
            logger.warning("Error reconnecting serial ports after Nav2: %s", e)

        with self._lock:
            self._is_exploring = False
            self.telemetry.state = "IDLE"
            self.telemetry.mode = "manual"
            self.telemetry.current_goal = None
            self.telemetry.distance_remaining_m = 0.0
        self._emit_telemetry()

    def navigate_to(self, x_m: float, y_m: float, theta_deg: float = 0.0) -> bool:
        """Send a 2D navigation waypoint target."""
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
        """Halt all autonomous motion and shut down Nav2 stack."""
        self._shutdown_nav2_stack()

        # Send immediate stop command to wheels
        try:
            wheels_svc = get_wheels_service()
            wheels_svc.stop()
        except Exception as e:
            logger.warning("Error stopping wheels: %s", e)

        if self._worker_thread and self._worker_thread.is_alive() and threading.current_thread() != self._worker_thread:
            self._worker_thread.join(timeout=1.0)
        self._worker_thread = None

        self._emit_log("Autonomous navigation stopped.")
        self._emit_telemetry()
        return True


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
