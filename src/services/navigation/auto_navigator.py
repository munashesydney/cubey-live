"""
Auto Navigator — Frontier Exploration State Machine & Trajectory Controller.

Coordinates autonomous exploration by combining real-time SLAM occupancy grids,
frontier extraction, A* path planning, reactive LiDAR safety braking, and
mecanum wheel trajectory execution.
"""

import enum
import logging
import math
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from src.services.lidar_service import LidarService, get_lidar_service
from src.services.navigation.frontier_detector import FrontierDetector
from src.services.navigation.path_planner import PathPlanner
from src.services.wheels_service import WheelsService, get_wheels_service

logger = logging.getLogger(__name__)


class NavigationState(str, enum.Enum):
    """Lifecycle states of the autonomous exploration engine."""
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    NAVIGATING = "NAVIGATING"
    OBSTACLE_AVOIDANCE = "OBSTACLE_AVOIDANCE"
    TELEOP_OVERRIDE = "TELEOP_OVERRIDE"
    COMPLETED = "COMPLETED"


class AutoNavigator:
    """
    Autonomous Exploration Manager.
    Drives Cubey through unknown rooms, following A* paths to open frontiers.
    """

    def __init__(
        self,
        mapping_service,
        wheels_service: Optional[WheelsService] = None,
        lidar_service: Optional[LidarService] = None,
        drive_speed: int = 160,              # Exploration drive speed (100-255)
        safety_stop_dist_mm: int = 280,       # Emergency obstacle brake distance
        waypoint_reach_dist_m: float = 0.12,  # 12cm waypoint arrival threshold
    ):
        self.mapping_service = mapping_service
        self.wheels_service = wheels_service or get_wheels_service()
        self.lidar_service = lidar_service or get_lidar_service()

        self.drive_speed = drive_speed
        self.safety_stop_dist_mm = safety_stop_dist_mm
        self.waypoint_reach_dist_m = waypoint_reach_dist_m

        self.frontier_detector = FrontierDetector(
            min_cluster_size=4,
            wall_clearance_cells=2,
            min_frontier_dist_m=0.35,
        )
        self.path_planner = PathPlanner(robot_radius_m=0.18, safety_margin_m=0.04)

        self.state = NavigationState.IDLE
        self.current_path: List[Tuple[float, float]] = []
        self.current_waypoint_idx: int = 0
        self.target_frontier: Optional[Tuple[float, float]] = None
        self._visited_targets: List[Tuple[float, float, float]] = []  # (x, y, timestamp)

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Teleoperation override watchdog
        self._teleop_override_until: float = 0.0

        # Exploration completion callback
        self.on_exploration_complete: Optional[Callable[[], None]] = None

    @property
    def is_active(self) -> bool:
        return self._running and self.state in (
            NavigationState.PLANNING,
            NavigationState.NAVIGATING,
            NavigationState.OBSTACLE_AVOIDANCE,
            NavigationState.TELEOP_OVERRIDE,
        )

    # ------------------------------------------------------------------
    # Lifecycle Controls
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start autonomous exploration in a background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self.state = NavigationState.PLANNING
            self.current_path.clear()
            self.current_waypoint_idx = 0
            self.target_frontier = None

            self._thread = threading.Thread(
                target=self._navigation_loop,
                daemon=True,
                name="AutoNavigatorWorker",
            )
            self._thread.start()
        logger.info("Autonomous Frontier Exploration started.")

    def stop(self) -> None:
        """Halt autonomous exploration and stop motors."""
        with self._lock:
            self._running = False
            self.state = NavigationState.IDLE
            self.current_path.clear()
            self.target_frontier = None

        self.wheels_service.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
        logger.info("Autonomous Frontier Exploration stopped.")

    def yield_to_teleop(self, duration_s: float = 3.0) -> None:
        """
        Temporarily pause autonomous driving when manual user teleoperation is detected.
        Automatically resumes auto-exploration after duration_s seconds of user inactivity.
        """
        self._teleop_override_until = time.time() + duration_s
        if self.state == NavigationState.NAVIGATING:
            self.state = NavigationState.TELEOP_OVERRIDE

    # ------------------------------------------------------------------
    # Main Navigation Loop
    # ------------------------------------------------------------------

    def _navigation_loop(self) -> None:
        """Main 10 Hz exploration and trajectory tracking loop."""
        replan_cooldown = 0.0

        while self._running:
            try:
                now = time.time()

                # 1. Check if user is manually driving (Teleop Override)
                if now < self._teleop_override_until:
                    self.state = NavigationState.TELEOP_OVERRIDE
                    time.sleep(0.10)
                    continue

                # 2. Check real-time LiDAR safety collision buffer (ignoring robot chassis < 170mm)
                min_front = self.lidar_service.latest_scan.min_front_dist_mm
                if 170 <= min_front < self.safety_stop_dist_mm:
                    if self.state == NavigationState.NAVIGATING:
                        logger.warning(
                            "Autonomous Navigation Obstacle Warning: %d mm in front. Halting.",
                            min_front,
                        )
                        self.wheels_service.stop()
                        self.state = NavigationState.OBSTACLE_AVOIDANCE
                        replan_cooldown = now + 1.5
                        time.sleep(0.20)
                        continue

                # 3. State Machine Execution
                if self.state in (NavigationState.PLANNING, NavigationState.OBSTACLE_AVOIDANCE, NavigationState.TELEOP_OVERRIDE):
                    if now >= replan_cooldown:
                        self._plan_next_frontier()

                elif self.state == NavigationState.NAVIGATING:
                    self._follow_path()

                elif self.state == NavigationState.COMPLETED:
                    self.wheels_service.stop()
                    break

            except Exception as e:
                logger.error("Error in AutoNavigator loop: %s", e, exc_info=True)

            time.sleep(0.08)

    # ------------------------------------------------------------------
    # Frontier Selection & Path Generation
    # ------------------------------------------------------------------

    def _plan_next_frontier(self) -> None:
        """Find open frontiers and generate an A* route to the best candidate."""
        grid = self.mapping_service.get_grid_copy()
        res_m = self.mapping_service.resolution_m
        origin_x = self.mapping_service.origin_x_m
        origin_y = self.mapping_service.origin_y_m
        robot_pose = self.mapping_service.pose

        # Find all open frontier clusters
        frontiers = self.frontier_detector.find_frontiers(
            grid=grid,
            resolution_m=res_m,
            origin_x_m=origin_x,
            origin_y_m=origin_y,
            robot_x_m=robot_pose.x_m,
            robot_y_m=robot_pose.y_m,
        )

        if not frontiers:
            # If map is fresh (less than 10 free cells explored), remain in PLANNING waiting for lidar scans
            explored_free = int(np.count_nonzero(grid == 0))
            if explored_free < 10:
                self.state = NavigationState.PLANNING
                return

            logger.info("No reachable frontiers found. Exploration complete.")
            self.state = NavigationState.COMPLETED
            self.wheels_service.stop()
            if self.on_exploration_complete:
                try:
                    self.on_exploration_complete()
                except Exception:
                    pass
            return

        # Attempt A* route to top ranked frontiers (filtering out targets visited in last 15s)
        now = time.time()
        # Clean expired visited targets (> 15s old)
        self._visited_targets = [v for v in self._visited_targets if (now - v[2]) < 15.0]

        planned_path = None
        chosen_target = None

        for candidate in frontiers[:8]:
            cx, cy = candidate.centroid_world
            # Skip if visited very recently
            if any(math.hypot(cx - vx, cy - vy) < 0.30 for vx, vy, _ in self._visited_targets):
                continue

            path = self.path_planner.plan_path(
                grid=grid,
                resolution_m=res_m,
                origin_x_m=origin_x,
                origin_y_m=origin_y,
                start_world=(robot_pose.x_m, robot_pose.y_m),
                goal_world=(cx, cy),
            )
            # Require at least 2 steps and meaningful total distance >= 25cm
            if path and len(path) >= 2:
                total_dist = sum(
                    math.hypot(path[i+1][0] - path[i][0], path[i+1][1] - path[i][1])
                    for i in range(len(path) - 1)
                )
                if total_dist >= 0.25:
                    planned_path = path
                    chosen_target = (cx, cy)
                    break

        if planned_path and chosen_target:
            with self._lock:
                self.current_path = planned_path
                self.current_waypoint_idx = 0
                self.target_frontier = chosen_target
                self.state = NavigationState.NAVIGATING
            logger.info(
                "Planned autonomous route with %d waypoints (dist=%.2fm) towards target %s",
                len(planned_path),
                total_dist,
                chosen_target,
            )
        else:
            # If path planner was blocked, rotate slightly to scan into unexplored space
            logger.info("No clear route to top frontiers. Rotating to discover open corridors.")
            self.wheels_service.set_speed(self.drive_speed)
            self.wheels_service.pulse("rotateRight", 300)
            time.sleep(0.4)

    # ------------------------------------------------------------------
    # Pure-Pursuit Waypoint Following & Smooth Motion Controller
    # ------------------------------------------------------------------

    def _follow_path(self) -> None:
        """Steer mecanum wheel base along the calculated A* waypoint route with look-ahead pure pursuit."""
        if not self.current_path:
            self.wheels_service.stop()
            self.state = NavigationState.PLANNING
            return

        robot_pose = self.mapping_service.pose
        LOOKAHEAD_DIST_M = 0.35  # Look ahead 35 cm along path for smooth curves

        # Advance waypoint index along path until finding a point >= LOOKAHEAD_DIST_M away
        while self.current_waypoint_idx < len(self.current_path) - 1:
            wp_x, wp_y = self.current_path[self.current_waypoint_idx]
            dist_to_wp = math.hypot(wp_x - robot_pose.x_m, wp_y - robot_pose.y_m)
            if dist_to_wp >= LOOKAHEAD_DIST_M:
                break
            self.current_waypoint_idx += 1

        # Check if arrived at final destination waypoint
        final_x, final_y = self.current_path[-1]
        dist_to_final = math.hypot(final_x - robot_pose.x_m, final_y - robot_pose.y_m)
        if dist_to_final < self.waypoint_reach_dist_m:
            logger.info("Reached frontier destination! Performing scan sweep...")
            self.wheels_service.stop()
            # Record visited target
            if self.target_frontier:
                self._visited_targets.append((self.target_frontier[0], self.target_frontier[1], time.time()))
            # Brief sweep rotation to clear LiDAR into the open room
            self.wheels_service.pulse("rotateRight", 250)
            time.sleep(0.3)
            self.state = NavigationState.PLANNING
            return

        # Target look-ahead waypoint
        target_x, target_y = self.current_path[self.current_waypoint_idx]
        dx = target_x - robot_pose.x_m
        dy = target_y - robot_pose.y_m

        # Target angle in world frame (0° = North / +Y, 90° = East / +X, clockwise)
        target_heading_rad = math.atan2(dx, dy)
        target_heading_deg = math.degrees(target_heading_rad) % 360.0

        # Heading error relative to robot's current orientation
        heading_diff = (target_heading_deg - robot_pose.theta_deg + 180.0) % 360.0 - 180.0

        self.wheels_service.set_speed(self.drive_speed)

        # Smooth Continuous Mecanum Steering
        if abs(heading_diff) > 55.0:
            # Significant angle mismatch: rotate smoothly towards target
            cmd = "rotateRight" if heading_diff > 0 else "rotateLeft"
            self.wheels_service.move(cmd)
        elif abs(heading_diff) > 18.0:
            # Moderate angle mismatch: curve smoothly forward
            cmd = "forwardRight" if heading_diff > 0 else "forwardLeft"
            self.wheels_service.move(cmd)
        else:
            # Aligned: drive straight forward
            self.wheels_service.move("forward")
