"""Fail-closed autonomous frontier exploration for Cubey.

No state may refresh motor commands indefinitely: sensor health, command
duration, progress, and recovery attempts are all bounded.
"""

from __future__ import annotations

import enum
import logging
import math
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from src.services.lidar_service import LidarPoint, LidarService, get_lidar_service
from src.services.navigation.frontier_detector import FrontierDetector
from src.services.navigation.path_planner import PathPlanner
from src.services.wheels_service import WheelsService, get_wheels_service

logger = logging.getLogger(__name__)


class NavigationState(str, enum.Enum):
    IDLE = "IDLE"
    WAITING_FOR_SENSORS = "WAITING_FOR_SENSORS"
    PLANNING = "PLANNING"
    NAVIGATING = "NAVIGATING"
    BACKTRACKING = "BACKTRACKING"
    RECOVERY = "RECOVERY"
    TELEOP_OVERRIDE = "TELEOP_OVERRIDE"
    COMPLETED = "COMPLETED"
    FAULT = "FAULT"
    E_STOPPED = "E_STOPPED"


class AutoNavigator:
    """Bounded frontier explorer with an independent LiDAR safety gate."""

    _ROTATION_COMMANDS = {"rotateLeft", "rotateRight"}
    _TRANSLATION_VECTORS = {
        "forward": (0.0, 1.0),
        "backward": (0.0, -1.0),
        "strafeLeft": (-1.0, 0.0),
        "strafeRight": (1.0, 0.0),
        "forwardLeft": (-1.0, 1.0),
        "forwardRight": (1.0, 1.0),
        "backwardLeft": (-1.0, -1.0),
        "backwardRight": (1.0, -1.0),
    }

    def __init__(
        self,
        mapping_service,
        wheels_service: Optional[WheelsService] = None,
        lidar_service: Optional[LidarService] = None,
        drive_speed: int = 90,
        recovery_speed: int = 80,
        backtrack_distance_m: float = 0.60,
        safety_stop_dist_mm: int = 350,
        waypoint_reach_dist_m: float = 0.12,
        max_scan_age_s: float = 0.35,
        max_wheel_telemetry_age_s: float = 1.0,
        min_scan_points: int = 30,
        sensor_start_timeout_s: float = 5.0,
        progress_timeout_s: float = 2.5,
        max_rotation_s: float = 1.8,
        max_recovery_attempts: int = 4,
        robot_length_m: float = 0.36,
        robot_width_m: float = 0.36,
        footprint_margin_m: float = 0.08,
    ):
        self.mapping_service = mapping_service
        self.wheels_service = wheels_service or get_wheels_service()
        self.lidar_service = lidar_service or get_lidar_service()

        self.drive_speed = max(60, min(255, int(drive_speed)))
        self.recovery_speed = max(60, min(255, int(recovery_speed)))
        self.backtrack_distance_m = max(0.25, float(backtrack_distance_m))
        self.safety_stop_dist_m = max(0.20, safety_stop_dist_mm / 1000.0)
        self.safety_stop_dist_mm = int(self.safety_stop_dist_m * 1000)
        self.waypoint_reach_dist_m = waypoint_reach_dist_m
        self.max_scan_age_s = max_scan_age_s
        self.max_wheel_telemetry_age_s = max_wheel_telemetry_age_s
        self.min_scan_points = min_scan_points
        self.sensor_start_timeout_s = sensor_start_timeout_s
        self.progress_timeout_s = progress_timeout_s
        self.max_rotation_s = max_rotation_s
        self.max_recovery_attempts = max_recovery_attempts
        self.robot_half_length_m = robot_length_m / 2.0
        self.robot_half_width_m = robot_width_m / 2.0
        self.footprint_margin_m = footprint_margin_m

        self.frontier_detector = FrontierDetector(
            min_cluster_size=4,
            wall_clearance_cells=2,
            min_frontier_dist_m=0.35,
        )
        self.path_planner = PathPlanner(
            robot_radius_m=max(robot_length_m, robot_width_m) / 2.0,
            safety_margin_m=footprint_margin_m,
        )

        self.state = NavigationState.IDLE
        self.fault_reason = ""
        self.current_path: List[Tuple[float, float]] = []
        self.current_waypoint_idx = 0
        self.target_frontier: Optional[Tuple[float, float]] = None
        self.is_backtracking: bool = False

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._teleop_override_until = 0.0
        self._healthy_scan_streak = 0
        self._start_deadline = 0.0
        self._recovery_attempts = 0
        self._replan_after = 0.0
        self._blocked_targets: List[Tuple[float, float, float]] = []
        self._last_command = "stop"
        self._last_progress_pose: Optional[Tuple[float, float, float]] = None
        self._last_progress_time = 0.0
        self._heading_turn_started = 0.0
        self.last_safety_rejection = ""
        self.last_safety_rejection_at = 0.0
        self.last_planning_rejection = ""

        self.on_exploration_complete: Optional[Callable[[], None]] = None

    @property
    def is_active(self) -> bool:
        return self._running and self.state in {
            NavigationState.WAITING_FOR_SENSORS,
            NavigationState.PLANNING,
            NavigationState.NAVIGATING,
            NavigationState.BACKTRACKING,
            NavigationState.RECOVERY,
            NavigationState.TELEOP_OVERRIDE,
        }

    def status(self) -> dict:
        healthy, health_reason = self._sensor_health()
        return {
            "state": self.state.value,
            "active": self.is_active,
            "is_backtracking": self.is_backtracking,
            "fault_reason": self.fault_reason,
            "emergency_stopped": self.wheels_service.is_emergency_stopped,
            "sensor_healthy": healthy,
            "sensor_health_reason": health_reason,
            "recovery_attempts": self._recovery_attempts,
            "last_safety_rejection": self.last_safety_rejection,
            "last_safety_rejection_at": self.last_safety_rejection_at,
            "last_planning_rejection": self.last_planning_rejection,
            "self_masked_lidar_points": sum(
                1
                for point in self.lidar_service.latest_scan.points
                if self._is_self_return(point)
            ),
        }

    # ------------------------------------------------------------------
    # Lifecycle and safety controls
    # ------------------------------------------------------------------

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            if self.wheels_service.is_emergency_stopped:
                self.state = NavigationState.E_STOPPED
                self.fault_reason = self.wheels_service.emergency_stop_reason
                return False

            if not self.wheels_service.is_connected:
                try:
                    self.wheels_service.connect()
                except Exception as exc:
                    self._set_fault(f"wheel_connection_failed:{exc}")
                    return False
            if not self.wheels_service.is_connected:
                self._set_fault("wheel_connection_failed")
                return False

            self._running = True
            self._stop_event.clear()
            self.state = NavigationState.WAITING_FOR_SENSORS
            self.fault_reason = ""
            self.current_path.clear()
            self.current_waypoint_idx = 0
            self.target_frontier = None
            self._healthy_scan_streak = 0
            self._recovery_attempts = 0
            self._last_command = "stop"
            self.last_planning_rejection = ""
            self._last_progress_pose = None
            self._last_progress_time = time.monotonic()
            self._start_deadline = time.monotonic() + self.sensor_start_timeout_s

            self._thread = threading.Thread(
                target=self._navigation_loop,
                daemon=True,
                name="AutoNavigatorWorker",
            )
            self._thread.start()
        logger.info("Fail-closed autonomous frontier exploration started.")
        return True

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_event.set()
            self.current_path.clear()
            self.target_frontier = None
            self._last_command = "stop"
            self.state = (
                NavigationState.E_STOPPED
                if self.wheels_service.is_emergency_stopped
                else NavigationState.IDLE
            )

        self.wheels_service.stop()
        if (
            self._thread
            and self._thread.is_alive()
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=1.0)
        self._thread = None

    def emergency_stop(self, reason: str = "operator_request") -> None:
        with self._lock:
            self.fault_reason = reason
            self.state = NavigationState.E_STOPPED
            self._running = False
            self._stop_event.set()
            self.current_path.clear()
            self.target_frontier = None
            self._last_command = "stop"
        self.wheels_service.emergency_stop(reason)
        logger.error("Autonomous emergency stop latched: %s", reason)

    def clear_emergency_stop(self) -> Tuple[bool, str]:
        if self._running:
            return False, "autonomy_must_be_stopped"
        if not self.wheels_service.is_connected:
            return False, "wheels_disconnected"
        # Re-arming is an operator-supervised action. Requiring the same wheel
        # telemetry or LiDAR stream that may have caused the stop creates an
        # unrecoverable lockout. Reset never resumes motion; subsequent manual
        # and autonomous commands still pass their own health gates.
        if not self.wheels_service.clear_emergency_stop():
            return False, "wheel_controller_reset_failed"
        with self._lock:
            self.fault_reason = ""
            self.state = NavigationState.IDLE
        return True, "cleared"

    def yield_to_teleop(self, duration_s: float = 3.0) -> bool:
        if self.wheels_service.is_emergency_stopped:
            return False
        self._teleop_override_until = time.monotonic() + max(0.0, duration_s)
        if self._running:
            self.state = NavigationState.TELEOP_OVERRIDE
        return True

    def authorize_manual_motion(self, command: str) -> Tuple[bool, str]:
        """Gate supervised motion without coupling it to autonomy health."""
        if command == "stop":
            return True, "ok"
        if self.wheels_service.is_emergency_stopped:
            return False, self.wheels_service.emergency_stop_reason
        if not self.wheels_service.is_connected:
            self._record_manual_rejection(command, "wheels_disconnected")
            return False, "wheels_disconnected"
        lidar_healthy, lidar_reason = self._lidar_health()
        if not lidar_healthy:
            self._record_manual_rejection(command, lidar_reason)
            return False, lidar_reason
        collision = self._collision_reason(command)
        if collision:
            # Reject this direction and cancel any prior continuous command,
            # while leaving the operator able to steer away from the obstacle.
            self.wheels_service.stop()
            self._record_manual_rejection(command, collision)
            return False, collision
        return True, "ok"

    def _record_manual_rejection(self, command: str, reason: str) -> None:
        self.last_safety_rejection = f"{command}:{reason}"
        self.last_safety_rejection_at = time.time()
        logger.warning("Manual motion rejected: command=%s reason=%s", command, reason)

    def _set_fault(self, reason: str) -> None:
        with self._lock:
            self.fault_reason = reason
            self.state = NavigationState.FAULT
            self._running = False
            self._stop_event.set()
            self.current_path.clear()
            self.target_frontier = None
            self._last_command = "stop"
        self.wheels_service.stop()
        logger.error("Autonomous navigation fault: %s", reason)

    # ------------------------------------------------------------------
    # Main control loop and health gates
    # ------------------------------------------------------------------

    def _sensor_health(self) -> Tuple[bool, str]:
        wheels_healthy, wheels_reason = self.wheels_service.telemetry_health(
            max_age_s=self.max_wheel_telemetry_age_s
        )
        if not wheels_healthy:
            return False, wheels_reason
        return self._lidar_health()

    def _lidar_health(self) -> Tuple[bool, str]:
        return self.lidar_service.scan_health(
            max_age_s=self.max_scan_age_s,
            min_points=self.min_scan_points,
            min_scan_rate_hz=2.0,
        )

    def _navigation_loop(self) -> None:
        while self._running and not self._stop_event.is_set():
            try:
                now = time.monotonic()
                if self.wheels_service.is_emergency_stopped:
                    self.state = NavigationState.E_STOPPED
                    self._running = False
                    break

                healthy, health_reason = self._sensor_health()
                if not healthy:
                    self._healthy_scan_streak = 0
                    if self.state in {
                        NavigationState.NAVIGATING,
                        NavigationState.BACKTRACKING,
                        NavigationState.RECOVERY,
                        NavigationState.TELEOP_OVERRIDE,
                    }:
                        self.emergency_stop(health_reason)
                        break
                    self.wheels_service.stop()
                    self._last_command = "stop"
                    self.state = NavigationState.WAITING_FOR_SENSORS
                    if now >= self._start_deadline:
                        self._set_fault(health_reason)
                        break
                    self._stop_event.wait(0.05)
                    continue

                self._healthy_scan_streak += 1
                if self.state == NavigationState.WAITING_FOR_SENSORS:
                    if self._healthy_scan_streak >= 3:
                        self.state = NavigationState.PLANNING
                    else:
                        self._stop_event.wait(0.05)
                        continue

                if now < self._teleop_override_until:
                    self.state = NavigationState.TELEOP_OVERRIDE
                    collision = self._collision_reason(self._telemetry_motion_command())
                    if collision:
                        self.emergency_stop(f"teleop_collision:{collision}")
                        break
                    self._stop_event.wait(0.05)
                    continue
                if self.state == NavigationState.TELEOP_OVERRIDE:
                    self.wheels_service.stop()
                    self._last_command = "stop"
                    self.state = NavigationState.PLANNING

                collision = self._collision_reason(self._last_command)
                if collision and self._last_command != "stop":
                    self._handle_obstacle(collision)
                    self._stop_event.wait(0.05)
                    continue

                if self.state in {NavigationState.PLANNING, NavigationState.RECOVERY}:
                    if now >= self._replan_after:
                        self._plan_next_frontier()
                elif self.state in {NavigationState.NAVIGATING, NavigationState.BACKTRACKING}:
                    self._follow_path()
                elif self.state == NavigationState.COMPLETED:
                    self.wheels_service.stop()
                    self._last_command = "stop"
                    self._running = False
                    break
                elif self.state in {NavigationState.FAULT, NavigationState.E_STOPPED}:
                    self._running = False
                    break
            except Exception as exc:
                logger.exception("Unhandled AutoNavigator error")
                self.emergency_stop(f"navigation_exception:{type(exc).__name__}")
                break

            self._stop_event.wait(0.05)

    def _telemetry_motion_command(self) -> str:
        return {
            "forward": "forward",
            "backward": "backward",
            "forward_left": "forwardLeft",
            "forward_right": "forwardRight",
            "backward_left": "backwardLeft",
            "backward_right": "backwardRight",
            "rotate_left": "rotateLeft",
            "rotate_right": "rotateRight",
            "strafe_left": "strafeLeft",
            "strafe_right": "strafeRight",
        }.get(self.wheels_service.telemetry.motion.lower(), "stop")

    # ------------------------------------------------------------------
    # Independent collision monitor
    # ------------------------------------------------------------------

    def _is_self_return(self, point: LidarPoint) -> bool:
        """True when a scan hit lies inside Cubey's measured physical body."""
        return (
            abs(point.x_m) <= self.robot_half_width_m
            and abs(point.y_m) <= self.robot_half_length_m
        )

    def _valid_points(self) -> List[LidarPoint]:
        return [
            point
            for point in self.lidar_service.latest_scan.points
            if point.quality > 0
            and point.distance_mm >= self.lidar_service.min_valid_distance_mm
            and not self._is_self_return(point)
        ]

    def _collision_reason(self, command: str) -> Optional[str]:
        if command == "stop":
            return None

        danger_points: List[LidarPoint] = []
        rotation_radius = math.hypot(
            self.robot_half_length_m, self.robot_half_width_m
        ) + self.footprint_margin_m
        base_clearance_m = max(
            0.05,
            self.safety_stop_dist_m
            - max(self.robot_half_length_m, self.robot_half_width_m),
        )

        for point in self._valid_points():
            x, y = point.x_m, point.y_m
            if command in self._ROTATION_COMMANDS:
                if math.hypot(x, y) <= rotation_radius:
                    danger_points.append(point)
            elif command in self._TRANSLATION_VECTORS:
                vector_x, vector_y = self._TRANSLATION_VECTORS[command]
                magnitude = math.hypot(vector_x, vector_y)
                velocity_x = vector_x / magnitude
                velocity_y = vector_y / magnitude

                # Project the rectangular body onto axes parallel and
                # perpendicular to the commanded travel direction. The danger
                # zone is the body swept forward by the stopping clearance.
                along = x * velocity_x + y * velocity_y
                cross = x * (-velocity_y) + y * velocity_x
                along_extent = (
                    abs(velocity_x) * self.robot_half_width_m
                    + abs(velocity_y) * self.robot_half_length_m
                )
                cross_extent = (
                    abs(velocity_y) * self.robot_half_width_m
                    + abs(velocity_x) * self.robot_half_length_m
                    + self.footprint_margin_m
                )
                if (
                    0.0 < along <= along_extent + base_clearance_m
                    and abs(cross) <= cross_extent
                ):
                    danger_points.append(point)

        if not danger_points:
            return None
        closest = min(danger_points, key=lambda point: point.distance_mm)
        return f"{command}:{closest.distance_mm:.0f}mm@{closest.angle_deg:.1f}deg"

    def _handle_obstacle(self, reason: str) -> None:
        self.wheels_service.stop()
        self._last_command = "stop"
        if self.target_frontier:
            self._block_target(self.target_frontier, duration_s=30.0)
        self.current_path.clear()
        self.current_waypoint_idx = 0
        self.is_backtracking = False
        self.state = NavigationState.RECOVERY
        self._recovery_attempts += 1
        self._replan_after = time.monotonic() + 0.5
        self._heading_turn_started = 0.0
        logger.warning("Collision monitor stopped autonomous motion: %s", reason)
        if self._recovery_attempts > self.max_recovery_attempts:
            if self._attempt_backtrack(f"recovery_limit_exceeded:{reason}"):
                return
            self._set_fault(f"recovery_limit_exceeded:{reason}")

    # ------------------------------------------------------------------
    # Frontier selection and bounded recovery
    # ------------------------------------------------------------------

    def _block_target(self, target: Tuple[float, float], duration_s: float = 45.0) -> None:
        self._blocked_targets.append(
            (target[0], target[1], time.monotonic() + duration_s)
        )

    def _target_is_blocked(self, target: Tuple[float, float]) -> bool:
        now = time.monotonic()
        self._blocked_targets = [item for item in self._blocked_targets if item[2] > now]
        return any(
            math.hypot(target[0] - x, target[1] - y) < 0.30
            for x, y, _ in self._blocked_targets
        )

    def _find_safe_backtrack_pose(
        self,
    ) -> Optional[Tuple[Tuple[float, float], List[Tuple[float, float]]]]:
        """
        Scans recent historical poses along the robot's trajectory to find a
        known-free waypoint with adequate rotational/safety clearance.
        Returns ((target_x, target_y), path) or None if no safe retreat is available.
        """
        pose = self.mapping_service.pose
        trajectory = list(self.mapping_service.trajectory)
        if len(trajectory) < 2:
            return None

        grid = self.mapping_service.get_grid_copy()
        inflated_mask = self.path_planner.inflate_obstacles(
            grid, resolution_m=self.mapping_service.resolution_m
        )

        min_retreat_m = max(0.20, self.backtrack_distance_m * 0.4)
        max_retreat_m = max(0.80, self.backtrack_distance_m * 2.5)

        # Search recent trajectory points backwards
        for tx, ty in reversed(trajectory[:-1]):
            dist = math.hypot(tx - pose.x_m, ty - pose.y_m)
            if dist < min_retreat_m:
                continue
            if dist > max_retreat_m:
                break

            gx, gy = self.mapping_service.world_to_grid(tx, ty)
            if not self.mapping_service.is_inside_grid(gx, gy):
                continue
            if grid[gy, gx] != 0 or inflated_mask[gy, gx]:
                continue

            path = self.path_planner.plan_path(
                grid=grid,
                resolution_m=self.mapping_service.resolution_m,
                origin_x_m=self.mapping_service.origin_x_m,
                origin_y_m=self.mapping_service.origin_y_m,
                start_world=(pose.x_m, pose.y_m),
                goal_world=(tx, ty),
            )
            if path and len(path) >= 2:
                return (tx, ty), path

        # Fallback: extract downsampled breadcrumbs directly from trajectory
        breadcrumb_path: List[Tuple[float, float]] = []
        for tx, ty in reversed(trajectory):
            dist = math.hypot(tx - pose.x_m, ty - pose.y_m)
            if (
                not breadcrumb_path
                or math.hypot(
                    tx - breadcrumb_path[-1][0], ty - breadcrumb_path[-1][1]
                )
                >= 0.08
            ):
                breadcrumb_path.append((tx, ty))
            if dist >= self.backtrack_distance_m:
                break

        if len(breadcrumb_path) >= 2:
            target = breadcrumb_path[-1]
            return target, breadcrumb_path

        return None

    def _attempt_backtrack(self, trigger_reason: str) -> bool:
        """
        Attempts a planned breadcrumb retreat back to a safe open pose.
        Returns True if backtrack navigation was successfully initiated.
        """
        backtrack = self._find_safe_backtrack_pose()
        if not backtrack:
            return False

        target, path = backtrack
        pose = self.mapping_service.pose

        # Verify rear sector is not physically blocked before reversing
        rear_block = self._collision_reason("backward")
        if rear_block:
            logger.warning("Backtrack rejected: rear collision risk (%s)", rear_block)
            return False

        # Quarantine current dead-end area
        self._block_target((pose.x_m, pose.y_m), duration_s=45.0)

        self.current_path = path
        self.current_waypoint_idx = 0
        self.target_frontier = target
        self.is_backtracking = True
        self.state = NavigationState.BACKTRACKING
        self._last_progress_pose = (pose.x_m, pose.y_m, pose.theta_deg)
        self._last_progress_time = time.monotonic()
        self._heading_turn_started = 0.0
        self.last_planning_rejection = ""
        logger.info(
            "Initiating breadcrumb backtrack (%s) to %s along %d waypoints",
            trigger_reason,
            target,
            len(path),
        )
        return True

    def _plan_next_frontier(self) -> None:
        grid = self.mapping_service.get_grid_copy()
        pose = self.mapping_service.pose
        frontiers = self.frontier_detector.find_frontiers(
            grid=grid,
            resolution_m=self.mapping_service.resolution_m,
            origin_x_m=self.mapping_service.origin_x_m,
            origin_y_m=self.mapping_service.origin_y_m,
            robot_x_m=pose.x_m,
            robot_y_m=pose.y_m,
        )

        if not frontiers:
            if int(np.count_nonzero(grid == 0)) < 10:
                self.state = NavigationState.PLANNING
                return
            self.wheels_service.stop()
            self._last_command = "stop"
            self.state = NavigationState.COMPLETED
            if self.on_exploration_complete:
                try:
                    self.on_exploration_complete()
                except Exception:
                    logger.exception("Exploration completion callback failed")
            return

        planning_rejections: List[str] = []
        for candidate in frontiers[:12]:
            target = candidate.centroid_world
            if self._target_is_blocked(target):
                continue
            path = self.path_planner.plan_path(
                grid=grid,
                resolution_m=self.mapping_service.resolution_m,
                origin_x_m=self.mapping_service.origin_x_m,
                origin_y_m=self.mapping_service.origin_y_m,
                start_world=(pose.x_m, pose.y_m),
                goal_world=target,
            )
            if not path or len(path) < 2:
                planning_rejections.append(
                    self.path_planner.last_failure_reason or "empty_path"
                )
                continue
            total_dist = sum(
                math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
                for i in range(len(path) - 1)
            )
            if total_dist < 0.25:
                planning_rejections.append("frontier_route_too_short")
                continue

            self.current_path = path
            self.current_waypoint_idx = 0
            self.target_frontier = target
            self.is_backtracking = False
            self.state = NavigationState.NAVIGATING
            self._last_progress_pose = (pose.x_m, pose.y_m, pose.theta_deg)
            self._last_progress_time = time.monotonic()
            self._heading_turn_started = 0.0
            self.last_planning_rejection = ""
            logger.info("Planned %d-waypoint route to %s", len(path), target)
            return

        if planning_rejections:
            # Expose the real planner decision in status/logs instead of only
            # reporting the eventual bounded-recovery wrapper.
            self.last_planning_rejection = max(
                set(planning_rejections), key=planning_rejections.count
            )
            logger.warning(
                "Rejected %d frontier routes: primary_reason=%s all_reasons=%s",
                len(planning_rejections),
                self.last_planning_rejection,
                sorted(set(planning_rejections)),
            )
        else:
            self.last_planning_rejection = "all_frontier_targets_temporarily_blocked"

        if self._recovery_attempts >= self.max_recovery_attempts:
            if self._attempt_backtrack(
                f"recovery_limit_exceeded:{self.last_planning_rejection}"
            ):
                return
            self._set_fault(
                "no_reachable_frontier_after_bounded_recovery:"
                f"{self.last_planning_rejection}"
            )
            return

        self._recovery_attempts += 1
        direction = "rotateRight" if self._recovery_attempts % 2 else "rotateLeft"
        rotation_block = self._collision_reason(direction)
        if rotation_block:
            opposite = "rotateLeft" if direction == "rotateRight" else "rotateRight"
            opposite_block = self._collision_reason(opposite)
            if opposite_block:
                if self._attempt_backtrack(
                    f"rotations_blocked:{rotation_block};{opposite_block}"
                ):
                    return
                self._set_fault(
                    "recovery_rotation_blocked_both_directions:"
                    f"{rotation_block};{opposite_block}"
                )
                return
            direction = opposite
        self._bounded_rotation(direction, duration_s=0.25)
        if self._running:
            self.state = NavigationState.PLANNING
            self._replan_after = time.monotonic() + 0.25

    def _bounded_rotation(self, direction: str, duration_s: float) -> None:
        self.state = NavigationState.RECOVERY
        self.wheels_service.set_speed(self.recovery_speed)
        if not self.wheels_service.move(direction):
            self._set_fault("recovery_motion_command_rejected")
            return
        self._last_command = direction
        deadline = time.monotonic() + min(duration_s, self.max_rotation_s)
        while self._running and not self._stop_event.is_set() and time.monotonic() < deadline:
            healthy, reason = self._sensor_health()
            if not healthy:
                self.emergency_stop(reason)
                return
            collision = self._collision_reason(direction)
            if collision:
                self.wheels_service.stop()
                self._last_command = "stop"
                return
            self._stop_event.wait(0.02)
        self.wheels_service.stop()
        self._last_command = "stop"

    # ------------------------------------------------------------------
    # Bounded path following
    # ------------------------------------------------------------------

    def _follow_path(self) -> None:
        if not self.current_path:
            self.wheels_service.stop()
            self._last_command = "stop"
            self.is_backtracking = False
            self.state = NavigationState.PLANNING
            return

        pose = self.mapping_service.pose
        now = time.monotonic()
        if self._last_progress_pose is None:
            self._last_progress_pose = (pose.x_m, pose.y_m, pose.theta_deg)
            self._last_progress_time = now

        px, py, ptheta = self._last_progress_pose
        translated = math.hypot(pose.x_m - px, pose.y_m - py)
        rotated = abs((pose.theta_deg - ptheta + 180.0) % 360.0 - 180.0)
        if translated >= 0.04 or rotated >= 4.0:
            self._last_progress_pose = (pose.x_m, pose.y_m, pose.theta_deg)
            self._last_progress_time = now
        elif now - self._last_progress_time > self.progress_timeout_s:
            self.wheels_service.stop()
            self._last_command = "stop"
            if self.target_frontier:
                self._block_target(self.target_frontier)
            self.current_path.clear()
            was_backtracking = self.is_backtracking
            self.is_backtracking = False
            self.state = NavigationState.RECOVERY
            self._recovery_attempts += 1
            self._replan_after = now + 0.5
            if self._recovery_attempts > self.max_recovery_attempts:
                if not was_backtracking and self._attempt_backtrack(
                    "progress_timeout_exhausted"
                ):
                    return
                self._set_fault("robot_not_making_progress")
            return

        lookahead_dist_m = 0.30
        while self.current_waypoint_idx < len(self.current_path) - 1:
            wp_x, wp_y = self.current_path[self.current_waypoint_idx]
            if math.hypot(wp_x - pose.x_m, wp_y - pose.y_m) >= lookahead_dist_m:
                break
            self.current_waypoint_idx += 1

        final_x, final_y = self.current_path[-1]
        if math.hypot(final_x - pose.x_m, final_y - pose.y_m) < self.waypoint_reach_dist_m:
            self.wheels_service.stop()
            self._last_command = "stop"
            if self.target_frontier and not self.is_backtracking:
                self._block_target(self.target_frontier, duration_s=15.0)
            self.current_path.clear()
            self.target_frontier = None
            self.is_backtracking = False
            self.state = NavigationState.PLANNING
            self._recovery_attempts = 0
            self._replan_after = now + 0.2
            return

        target_x, target_y = self.current_path[self.current_waypoint_idx]
        target_heading_deg = math.degrees(
            math.atan2(target_x - pose.x_m, target_y - pose.y_m)
        ) % 360.0

        if self.is_backtracking:
            # Reversing into waypoints without rotating 180 deg
            # Rear facing direction in world coordinates is (pose.theta_deg + 180.0)
            reverse_heading_error = (
                target_heading_deg - (pose.theta_deg + 180.0) + 180.0
            ) % 360.0 - 180.0

            if abs(reverse_heading_error) > 55.0:
                rot_dir = "rotateRight" if reverse_heading_error > 0 else "rotateLeft"
                if not self._collision_reason(rot_dir):
                    command = rot_dir
                    if self._heading_turn_started == 0.0:
                        self._heading_turn_started = now
                    elif now - self._heading_turn_started > self.max_rotation_s:
                        self.wheels_service.stop()
                        self._last_command = "stop"
                        self.current_path.clear()
                        self.is_backtracking = False
                        self.state = NavigationState.RECOVERY
                        self._recovery_attempts += 1
                        self._replan_after = now + 0.5
                        return
                else:
                    command = "strafeRight" if reverse_heading_error < 0 else "strafeLeft"
                    self._heading_turn_started = 0.0
            elif abs(reverse_heading_error) > 18.0:
                command = "backwardRight" if reverse_heading_error < 0 else "backwardLeft"
                self._heading_turn_started = 0.0
            else:
                command = "backward"
                self._heading_turn_started = 0.0
        else:
            heading_error = (target_heading_deg - pose.theta_deg + 180.0) % 360.0 - 180.0
            if abs(heading_error) > 55.0:
                command = "rotateRight" if heading_error > 0 else "rotateLeft"
                if self._heading_turn_started == 0.0:
                    self._heading_turn_started = now
                elif now - self._heading_turn_started > self.max_rotation_s:
                    self.wheels_service.stop()
                    self._last_command = "stop"
                    if self.target_frontier:
                        self._block_target(self.target_frontier)
                    self.current_path.clear()
                    self.state = NavigationState.RECOVERY
                    self._recovery_attempts += 1
                    self._replan_after = now + 0.5
                    return
            elif abs(heading_error) > 18.0:
                command = "forwardRight" if heading_error > 0 else "forwardLeft"
                self._heading_turn_started = 0.0
            else:
                command = "forward"
                self._heading_turn_started = 0.0

        collision = self._collision_reason(command)
        if collision:
            self._handle_obstacle(collision)
            return

        speed = self.recovery_speed if self.is_backtracking else self.drive_speed
        self.wheels_service.set_speed(speed)
        if not self.wheels_service.move(command):
            if self.wheels_service.is_emergency_stopped:
                self.state = NavigationState.E_STOPPED
                self._running = False
            else:
                self._set_fault("wheel_motion_command_rejected")
            return
        self._last_command = command
