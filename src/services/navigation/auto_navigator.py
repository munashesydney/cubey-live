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
        safety_stop_dist_mm: int = 260,       # Emergency obstacle brake distance
        waypoint_reach_dist_m: float = 0.20,  # 20cm waypoint arrival threshold
    ):
        self.mapping_service = mapping_service
        self.wheels_service = wheels_service or get_wheels_service()
        self.lidar_service = lidar_service or get_lidar_service()

        self.drive_speed = drive_speed
        self.safety_stop_dist_mm = safety_stop_dist_mm
        self.waypoint_reach_dist_m = waypoint_reach_dist_m

        self.frontier_detector = FrontierDetector(min_cluster_size=3, wall_clearance_cells=2)
        self.path_planner = PathPlanner(robot_radius_m=0.20, safety_margin_m=0.05)

        self.state = NavigationState.IDLE
        self.current_path: List[Tuple[float, float]] = []
        self.current_waypoint_idx: int = 0
        self.target_frontier: Optional[Tuple[float, float]] = None

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

                # 2. Check real-time LiDAR safety collision buffer
                min_front = self.lidar_service.latest_scan.min_front_dist_mm
                if min_front < self.safety_stop_dist_mm:
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

            time.sleep(0.10)

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

        # Attempt A* route to top ranked frontiers
        planned_path = None
        chosen_target = None

        for candidate in frontiers[:5]:
            path = self.path_planner.plan_path(
                grid=grid,
                resolution_m=res_m,
                origin_x_m=origin_x,
                origin_y_m=origin_y,
                start_world=(robot_pose.x_m, robot_pose.y_m),
                goal_world=candidate.centroid_world,
            )
            if path and len(path) >= 2:
                planned_path = path
                chosen_target = candidate.centroid_world
                break

        if planned_path and chosen_target:
            with self._lock:
                self.current_path = planned_path
                self.current_waypoint_idx = 1  # Skip start point (robot's current pose)
                self.target_frontier = chosen_target
                self.state = NavigationState.NAVIGATING
            logger.info(
                "Planned autonomous route with %d waypoints towards target %s",
                len(planned_path),
                chosen_target,
            )
        else:
            # If path planner was blocked by tight inflation, spin slightly to clear view
            logger.info("Frontier path blocked by narrow corridor. Rotating to clear view.")
            self.wheels_service.set_speed(self.drive_speed)
            self.wheels_service.pulse("rotateRight", 300)
            time.sleep(0.4)

    # ------------------------------------------------------------------
    # Waypoint Following & Mecanum Trajectory Controller
    # ------------------------------------------------------------------

    def _follow_path(self) -> None:
        """Steer mecanum wheel base along the calculated A* waypoint route."""
        if not self.current_path or self.current_waypoint_idx >= len(self.current_path):
            # Reached end of path! Re-evaluate frontiers
            self.wheels_service.stop()
            self.state = NavigationState.PLANNING
            return

        target_x, target_y = self.current_path[self.current_waypoint_idx]
        robot_pose = self.mapping_service.pose

        dx = target_x - robot_pose.x_m
        dy = target_y - robot_pose.y_m
        dist = math.hypot(dx, dy)

        # Check if arrived at current waypoint
        if dist < self.waypoint_reach_dist_m:
            self.current_waypoint_idx += 1
            if self.current_waypoint_idx >= len(self.current_path):
                self.wheels_service.stop()
                self.state = NavigationState.PLANNING
                return
            target_x, target_y = self.current_path[self.current_waypoint_idx]
            dx = target_x - robot_pose.x_m
            dy = target_y - robot_pose.y_m

        # Target angle in world frame (0° = North / +Y, 90° = East / +X, clockwise)
        target_heading_rad = math.atan2(dx, dy)
        target_heading_deg = math.degrees(target_heading_rad) % 360.0

        # Heading error relative to robot's current orientation
        heading_diff = (target_heading_deg - robot_pose.theta_deg + 180.0) % 360.0 - 180.0

        self.wheels_service.set_speed(self.drive_speed)

        # Mecanum Steering Logic
        if abs(heading_diff) > 40.0:
            # Significant angle mismatch: rotate in place to face target
            cmd = "rotateRight" if heading_diff > 0 else "rotateLeft"
            self.wheels_service.pulse(cmd, 180)
        elif abs(heading_diff) > 18.0:
            # Moderate angle mismatch: curve forward
            cmd = "forwardRight" if heading_diff > 0 else "forwardLeft"
            self.wheels_service.pulse(cmd, 220)
        else:
            # Aligned: drive straight forward
            self.wheels_service.pulse("forward", 220)
