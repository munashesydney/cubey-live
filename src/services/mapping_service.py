"""
Mapping Service — 2D SLAM, Occupancy Grid Generator, and Robot Pose Tracker.

Maintains a 2D probability occupancy grid (e.g. 20m x 20m @ 5cm resolution),
correlates 360° RPLIDAR C1 scans with LiDAR scan-matching to track robot pose (X, Y, Theta),
raycasts free space vs obstacle boundaries, records trajectory trails, and persists
maps to SQLite.
"""

import math
import logging
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from src.db.models.map import MapModel
from src.db.repositories.map_repository import (
    create_map,
    get_active_map,
    get_map,
    list_maps,
    set_active_map,
    update_map,
)
from src.services.lidar_service import LidarPoint, LidarScanData, get_lidar_service

logger = logging.getLogger(__name__)


@dataclass
class RobotPose:
    """Robot position and orientation in the world coordinate frame."""
    x_m: float = 0.0          # Meters (East / Right is +X)
    y_m: float = 0.0          # Meters (North / Forward is +Y)
    theta_deg: float = 0.0    # Degrees (0° = North, 90° = East, clockwise)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x_m": round(self.x_m, 3),
            "y_m": round(self.y_m, 3),
            "theta_deg": round(self.theta_deg, 1),
            "timestamp": self.timestamp,
        }


@dataclass
class MappingSnapshot:
    """Real-time broadcast payload for web clients."""
    pose: RobotPose
    trajectory: List[Tuple[float, float]]  # List of (x, y) path coordinates
    laser_scan: List[Tuple[float, float]]   # Global (x, y) hit points
    width: int
    height: int
    resolution_cm: float
    origin_x_m: float
    origin_y_m: float
    is_mapping: bool
    map_name: str
    active_map_id: Optional[int]
    total_explored_cells: int
    planned_path: List[Tuple[float, float]] = field(default_factory=list)
    target_frontier: Optional[Tuple[float, float]] = None
    timestamp: float = field(default_factory=time.time)


class MappingService:
    """
    Coordinates 2D SLAM occupancy grid mapping, LiDAR scan-matching pose estimation,
    autonomous frontier exploration, and map persistence.
    """

    def __init__(
        self,
        width: int = 400,
        height: int = 400,
        resolution_m: float = 0.05,  # 5cm per pixel -> 20m x 20m map
        lidar_service=None,
        wheels_service=None,
        autonomy_enabled: Optional[bool] = None,
        robot_length_m: Optional[float] = None,
        robot_width_m: Optional[float] = None,
        no_return_clear_range_m: float = 0.75,
    ):
        from src.config import config

        self.width = width
        self.height = height
        self.resolution_m = resolution_m
        self.resolution_cm = resolution_m * 100.0

        # Origin offset so (0,0) world is centered in the grid
        self.origin_x_m = -(self.width * self.resolution_m) / 2.0
        self.origin_y_m = -(self.height * self.resolution_m) / 2.0

        self.lidar_service = lidar_service or get_lidar_service()
        self.robot_half_length_m = (
            float(robot_length_m)
            if robot_length_m is not None
            else config.robot_length_m
        ) / 2.0
        self.robot_half_width_m = (
            float(robot_width_m)
            if robot_width_m is not None
            else config.robot_width_m
        ) / 2.0
        # A zero-distance C1 sample means no reflected obstacle return.  Clear
        # only a short local corridor so frontier exploration can advance into
        # an open doorway while every movement remains protected by the fresh
        # live-scan collision gate.  This is intentionally far below the C1's
        # advertised maximum range.
        self.no_return_clear_range_m = max(
            0.40, min(1.50, float(no_return_clear_range_m))
        )

        # Log-odds probability grid (-5.0 = free, +5.0 = occupied, 0.0 = unknown)
        self._log_odds = np.zeros((self.height, self.width), dtype=np.float32)
        # Display grid: -1 = unknown, 0 = free, 100 = occupied
        self._grid = np.full((self.height, self.width), -1, dtype=np.int8)

        self._lock = threading.Lock()
        self.pose = RobotPose(0.0, 0.0, 0.0)
        self.trajectory: List[Tuple[float, float]] = [(0.0, 0.0)]
        self._latest_hits: List[Tuple[float, float]] = []
        self.last_scan_match_score = 0.0
        self.last_scan_match_points = 0
        self.last_scan_match_accepted = False
        self.last_scan_match_reason = "map_bootstrap"

        self.is_mapping: bool = False
        self.active_map_id: Optional[int] = None
        self.map_name: str = "Live Floorplan"

        self.on_snapshot: Optional[Callable[[MappingSnapshot], None]] = None

        # Autonomous Frontier Navigation Manager. Motion is opt-in after
        # commissioning; mapping-only mode remains available by default.
        from src.services.navigation import AutoNavigator
        config.validate_navigation()
        self.autonomy_enabled = (
            config.nav_autonomy_enabled
            if autonomy_enabled is None
            else bool(autonomy_enabled)
        )
        self.navigator = AutoNavigator(
            mapping_service=self,
            lidar_service=self.lidar_service,
            wheels_service=wheels_service,
            drive_speed=config.nav_drive_speed,
            recovery_speed=config.nav_recovery_speed,
            backtrack_distance_m=config.nav_backtrack_distance_m,
            safety_stop_dist_mm=config.lidar_safety_distance_mm,
            max_scan_age_s=config.nav_max_scan_age_s,
            max_wheel_telemetry_age_s=config.nav_max_wheel_telemetry_age_s,
            min_scan_points=config.nav_min_scan_points,
            sensor_start_timeout_s=config.nav_sensor_start_timeout_s,
            progress_timeout_s=config.nav_progress_timeout_s,
            max_rotation_s=config.nav_max_rotation_s,
            max_recovery_attempts=config.nav_max_recovery_attempts,
            robot_length_m=self.robot_half_length_m * 2.0,
            robot_width_m=self.robot_half_width_m * 2.0,
            footprint_margin_m=config.robot_footprint_margin_m,
        )

        # Subscribe to LiDAR scan stream
        self._original_lidar_callback = self.lidar_service.on_scan_data
        self.lidar_service.on_scan_data = self._handle_lidar_scan

        # Load active map from SQLite if one exists
        try:
            self._load_active_map_from_db()
        except Exception as e:
            logger.warning("Could not load initial active map: %s", e)

    # ------------------------------------------------------------------
    # Coordinate Transformations
    # ------------------------------------------------------------------

    def world_to_grid(self, x_m: float, y_m: float) -> Tuple[int, int]:
        """Convert real-world meters (X, Y) to grid cell indices (gx, gy)."""
        gx = int((x_m - self.origin_x_m) / self.resolution_m)
        gy = int((y_m - self.origin_y_m) / self.resolution_m)
        return gx, gy

    def grid_to_world(self, gx: int, gy: int) -> Tuple[float, float]:
        """Convert grid cell (gx, gy) to real-world meters (X, Y)."""
        x_m = (gx + 0.5) * self.resolution_m + self.origin_x_m
        y_m = (gy + 0.5) * self.resolution_m + self.origin_y_m
        return x_m, y_m

    def is_inside_grid(self, gx: int, gy: int) -> bool:
        """Check whether cell coordinates fall within map boundaries."""
        return 0 <= gx < self.width and 0 <= gy < self.height

    def is_robot_self_return(self, point: LidarPoint) -> bool:
        """Exclude chassis/mount reflections inside the measured robot body."""
        return (
            abs(point.x_m) <= self.robot_half_width_m
            and abs(point.y_m) <= self.robot_half_length_m
        )

    # ------------------------------------------------------------------
    # Mapping & Autonomous Exploration Controls
    # ------------------------------------------------------------------

    def start_mapping(self) -> bool:
        """Enable active SLAM map updates and launch autonomous frontier exploration."""
        with self._lock:
            self.is_mapping = True

        # Ensure LiDAR hardware is connected and active
        try:
            if not self.lidar_service.is_connected:
                self.lidar_service.connect()
            if not self.lidar_service.is_scanning:
                self.lidar_service.start_scan()
        except Exception as e:
            logger.warning("Could not auto-start LiDAR for mapping: %s", e)

        # Launch autonomous navigation
        if self.autonomy_enabled:
            autonomous_started = self.navigator.start()
        else:
            self.navigator.stop()
            autonomous_started = False
            logger.warning(
                "Mapping started in mapping-only mode; set NAV_AUTONOMY_ENABLED=true after commissioning"
            )
        logger.info("2D House Mapping & Autonomous Exploration session started.")
        return autonomous_started

    def pause_mapping(self) -> None:
        """Pause grid updates and autonomous exploration while keeping current map state."""
        with self._lock:
            self.is_mapping = False
        self.navigator.stop()
        logger.info("2D House Mapping session paused.")

    def reset_map(self) -> None:
        """Clear grid back to unexplored space, reset robot pose, and stop autonomous navigation."""
        self.navigator.stop()
        with self._lock:
            self._log_odds.fill(0.0)
            self._grid.fill(-1)
            self.pose = RobotPose(0.0, 0.0, 0.0)
            self.trajectory = [(0.0, 0.0)]
            self._latest_hits.clear()
            self.last_scan_match_score = 0.0
            self.last_scan_match_points = 0
            self.last_scan_match_accepted = False
            self.last_scan_match_reason = "map_reset"
            self.active_map_id = None
            self.map_name = "Live Floorplan"
        logger.info("Occupancy grid map reset.")

    # ------------------------------------------------------------------
    # Scan Matching & Pose Estimation (LiDAR Odometry)
    # ------------------------------------------------------------------

    @staticmethod
    def _ordered_symmetric_offsets(max_value: float, step: float) -> List[float]:
        offsets = [0.0]
        count = int(round(max_value / step))
        for index in range(1, count + 1):
            value = round(index * step, 6)
            offsets.extend((-value, value))
        return offsets

    def _current_motion_command(self) -> str:
        """Return the freshest normalized command available to scan matching."""
        navigator_command = str(
            getattr(getattr(self, "navigator", None), "_last_command", "stop")
        )
        if navigator_command.lower() != "stop":
            return navigator_command.lower()
        telemetry = getattr(
            getattr(getattr(self, "navigator", None), "wheels_service", None),
            "telemetry",
            None,
        )
        return str(getattr(telemetry, "motion", "stop")).lower().replace("_", "")

    def _scan_match_candidates(
        self, motion_command: str
    ) -> Tuple[List[Tuple[float, float]], List[float]]:
        """Generate a motion-bounded search window with the current pose first."""
        command = motion_command.lower().replace("_", "")
        if command in {"rotateleft", "rotateright"}:
            positions = [(0.0, 0.0), (-0.02, 0.0), (0.02, 0.0), (0.0, -0.02), (0.0, 0.02)]
            angles = self._ordered_symmetric_offsets(14.0, 2.0)
            return positions, angles

        translation_vectors = {
            "forward": (0.0, 1.0),
            "backward": (0.0, -1.0),
            "strafeleft": (-1.0, 0.0),
            "straferight": (1.0, 0.0),
            "forwardleft": (-1.0, 1.0),
            "forwardright": (1.0, 1.0),
            "backwardleft": (-1.0, -1.0),
            "backwardright": (1.0, -1.0),
        }
        vector = translation_vectors.get(command)
        if vector:
            vx, vy = vector
            magnitude = math.hypot(vx, vy)
            vx, vy = vx / magnitude, vy / magnitude
            pose_rad = math.radians(self.pose.theta_deg)
            sin_p, cos_p = math.sin(pose_rad), math.cos(pose_rad)
            positions = [(0.0, 0.0)]
            for along in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12):
                for lateral in (0.0, -0.02, 0.02):
                    local_x = vx * along - vy * lateral
                    local_y = vy * along + vx * lateral
                    world_dx = local_x * cos_p + local_y * sin_p
                    world_dy = -local_x * sin_p + local_y * cos_p
                    positions.append((world_dx, world_dy))
            angles = self._ordered_symmetric_offsets(8.0, 2.0)
            return positions, angles

        # When stopped or motion is unknown, permit only tiny corrections. A
        # weak scan must never invent continuous robot movement.
        positions = [
            (0.0, 0.0),
            (-0.02, 0.0),
            (0.02, 0.0),
            (0.0, -0.02),
            (0.0, 0.02),
        ]
        angles = self._ordered_symmetric_offsets(2.0, 1.0)
        return positions, angles

    def _build_correlation_field(self) -> np.ndarray:
        """Create a small distance-tolerant wall likelihood field."""
        occupied = self._grid == 100
        correlation = np.zeros(self._grid.shape, dtype=np.float32)
        correlation[self._grid == 0] = -0.12
        height, width = self._grid.shape

        for dy in range(-2, 3):
            for dx in range(-2, 3):
                distance = math.hypot(dx, dy)
                if distance > 2.01:
                    continue
                weight = 1.0 if distance == 0 else (0.65 if distance <= 1.42 else 0.30)

                src_y0 = max(0, -dy)
                src_y1 = min(height, height - dy)
                src_x0 = max(0, -dx)
                src_x1 = min(width, width - dx)
                dst_y0, dst_y1 = src_y0 + dy, src_y1 + dy
                dst_x0, dst_x1 = src_x0 + dx, src_x1 + dx
                source = occupied[src_y0:src_y1, src_x0:src_x1]
                target = correlation[dst_y0:dst_y1, dst_x0:dst_x1]
                target[source] = np.maximum(target[source], weight)

        return correlation

    def _scan_match(self, scan_points: List[LidarPoint]) -> RobotPose:
        """
        Correlates laser hits against the existing occupancy grid to refine
        robot position (X, Y, Theta).
        """
        # If map is fresh (less than 20 occupied cells), return current pose
        occupied_count = np.count_nonzero(self._grid == 100)
        if occupied_count < 20:
            self.last_scan_match_accepted = False
            self.last_scan_match_reason = "map_bootstrap"
            return RobotPose(
                self.pose.x_m,
                self.pose.y_m,
                self.pose.theta_deg,
                timestamp=time.time(),
            )

        # Sample a subset of high-quality points to speed up correlation
        valid_points = [
            point
            for point in scan_points
            if 200 <= point.distance_mm <= 10000
            and point.quality > 15
            and not self.is_robot_self_return(point)
        ]
        if len(valid_points) > 100:
            indices = np.linspace(0, len(valid_points) - 1, 100, dtype=int)
            valid_points = [valid_points[index] for index in indices]

        if len(valid_points) < 8:
            self.last_scan_match_accepted = False
            self.last_scan_match_reason = "insufficient_quality_points"
            return RobotPose(
                self.pose.x_m,
                self.pose.y_m,
                self.pose.theta_deg,
                timestamp=time.time(),
            )

        local_x = np.array([point.x_m for point in valid_points], dtype=np.float32)
        local_y = np.array([point.y_m for point in valid_points], dtype=np.float32)
        correlation = self._build_correlation_field()
        positions, angle_offsets = self._scan_match_candidates(
            self._current_motion_command()
        )

        best_score = -float("inf")
        best_matches = 0
        best_delta = (0.0, 0.0, 0.0)
        current_score = -float("inf")
        current_matches = 0

        for delta_theta in angle_offsets:
            theta = self.pose.theta_deg + delta_theta
            theta_rad = math.radians(theta)
            sin_theta, cos_theta = math.sin(theta_rad), math.cos(theta_rad)
            rotated_x = local_x * cos_theta + local_y * sin_theta
            rotated_y = -local_x * sin_theta + local_y * cos_theta

            for delta_x, delta_y in positions:
                candidate_x = self.pose.x_m + delta_x
                candidate_y = self.pose.y_m + delta_y
                grid_x = (
                    (candidate_x + rotated_x - self.origin_x_m)
                    / self.resolution_m
                ).astype(np.int32)
                grid_y = (
                    (candidate_y + rotated_y - self.origin_y_m)
                    / self.resolution_m
                ).astype(np.int32)
                inside = (
                    (grid_x >= 0)
                    & (grid_x < self.width)
                    & (grid_y >= 0)
                    & (grid_y < self.height)
                )
                if int(np.count_nonzero(inside)) < len(valid_points) * 0.8:
                    continue
                values = correlation[grid_y[inside], grid_x[inside]]
                score = float(np.sum(values) / len(valid_points))
                matches = int(np.count_nonzero(values >= 0.30))

                if delta_x == 0.0 and delta_y == 0.0 and delta_theta == 0.0:
                    current_score = score
                    current_matches = matches

                if score > best_score + 1e-6 or (
                    abs(score - best_score) <= 1e-6 and matches > best_matches
                ):
                    best_score = score
                    best_matches = matches
                    best_delta = (delta_x, delta_y, delta_theta)

        if not math.isfinite(best_score):
            self.last_scan_match_score = 0.0
            self.last_scan_match_points = 0
            self.last_scan_match_accepted = False
            self.last_scan_match_reason = "scan_outside_map_bounds"
            return RobotPose(
                self.pose.x_m,
                self.pose.y_m,
                self.pose.theta_deg,
                timestamp=time.time(),
            )

        self.last_scan_match_score = round(best_score, 4)
        self.last_scan_match_points = best_matches
        minimum_matches = max(6, int(math.ceil(len(valid_points) * 0.08)))
        if best_matches < minimum_matches:
            self.last_scan_match_accepted = False
            self.last_scan_match_reason = "insufficient_map_correspondence"
            return RobotPose(
                self.pose.x_m,
                self.pose.y_m,
                self.pose.theta_deg,
                timestamp=time.time(),
            )

        delta_x, delta_y, delta_theta = best_delta
        pose_changed = any(
            abs(value) > 1e-9 for value in (delta_x, delta_y, delta_theta)
        )
        if pose_changed and current_matches >= minimum_matches:
            if best_score - current_score < 0.004:
                self.last_scan_match_accepted = False
                self.last_scan_match_reason = "no_confident_improvement"
                return RobotPose(
                    self.pose.x_m,
                    self.pose.y_m,
                    self.pose.theta_deg,
                    timestamp=time.time(),
                )

        self.last_scan_match_accepted = pose_changed
        self.last_scan_match_reason = (
            "pose_correction_accepted" if pose_changed else "current_pose_best"
        )
        return RobotPose(
            x_m=self.pose.x_m + delta_x,
            y_m=self.pose.y_m + delta_y,
            theta_deg=(self.pose.theta_deg + delta_theta) % 360.0,
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------
    # Raycasting (Bresenham's Line Algorithm)
    # ------------------------------------------------------------------

    def _raycast_update(
        self,
        robot_x: float,
        robot_y: float,
        scan_points: List[LidarPoint],
        clear_ray_angles_deg: Optional[List[float]] = None,
    ) -> List[Tuple[float, float]]:
        """
        Clears free cells along laser rays and marks solid obstacle endpoints in the grid.
        Returns list of global world (x, y) hit points.
        """
        L_OCC = 1.2    # Log-odds increase for obstacle hits
        L_FREE = -0.4  # Log-odds decrease for unknown/free space
        # A reflected wall should not disappear because a few neighboring rays
        # cross its old cell after millimeter-scale pose/range noise.
        L_FREE_THROUGH_OCCUPIED = -0.08
        MAX_LOG_ODDS = 5.0
        MIN_LOG_ODDS = -5.0

        gx0, gy0 = self.world_to_grid(robot_x, robot_y)
        if not self.is_inside_grid(gx0, gy0):
            return []

        hit_world_coords: List[Tuple[float, float]] = []

        pose_rad = math.radians(self.pose.theta_deg)
        sin_p = math.sin(pose_rad)
        cos_p = math.cos(pose_rad)
        valid_hit_rays: List[Tuple[float, float]] = []

        def update_free_cell(cell_x: int, cell_y: int) -> None:
            current = float(self._log_odds[cell_y, cell_x])
            decrement = (
                L_FREE_THROUGH_OCCUPIED if current > 0.6 else L_FREE
            )
            self._log_odds[cell_y, cell_x] = max(
                MIN_LOG_ODDS, current + decrement
            )

        def trace_free_ray(
            target_x: int,
            target_y: int,
            *,
            include_endpoint: bool,
            stop_at_observed_wall: bool,
        ) -> None:
            dx = abs(target_x - gx0)
            dy = abs(target_y - gy0)
            sx = 1 if gx0 < target_x else -1
            sy = 1 if gy0 < target_y else -1
            err = dx - dy
            curr_x, curr_y = gx0, gy0

            while True:
                at_endpoint = curr_x == target_x and curr_y == target_y
                if at_endpoint and not include_endpoint:
                    break
                if not self.is_inside_grid(curr_x, curr_y):
                    break
                if (
                    stop_at_observed_wall
                    and (curr_x, curr_y) != (gx0, gy0)
                    and self._log_odds[curr_y, curr_x] > 0.6
                ):
                    break
                update_free_cell(curr_x, curr_y)
                if at_endpoint:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    curr_x += sx
                if e2 < dx:
                    err += dx
                    curr_y += sy

        for pt in scan_points:
            dist_m = pt.distance_mm / 1000.0
            if dist_m < (self.lidar_service.min_valid_distance_mm / 1000.0) or dist_m > 14.0:
                continue
            if self.is_robot_self_return(pt):
                continue

            pt_rad = math.radians(pt.angle_deg)
            lx = dist_m * math.sin(pt_rad)
            ly = dist_m * math.cos(pt_rad)

            # Transform into world coordinates
            wx = robot_x + lx * cos_p + ly * sin_p
            wy = robot_y - lx * sin_p + ly * cos_p

            hit_world_coords.append((round(wx, 3), round(wy, 3)))

            gx1, gy1 = self.world_to_grid(wx, wy)
            trace_free_ray(
                gx1,
                gy1,
                include_endpoint=False,
                stop_at_observed_wall=False,
            )

            # Mark endpoint as occupied
            if self.is_inside_grid(gx1, gy1) and pt.quality > 10:
                self._log_odds[gy1, gx1] = min(
                    MAX_LOG_ODDS, self._log_odds[gy1, gx1] + L_OCC
                )
                valid_hit_rays.append((pt.angle_deg % 360.0, dist_m))

        # Fill safe free-space slivers between nearby reflected samples. A raw
        # one-cell Bresenham ray per return creates the large starburst pattern
        # seen in the browser. Interpolation never marks an obstacle and stops
        # short of the nearer measured endpoint.
        if len(valid_hit_rays) >= 2:
            valid_hit_rays.sort(key=lambda item: item[0])
            wrapped_rays = valid_hit_rays + [
                (valid_hit_rays[0][0] + 360.0, valid_hit_rays[0][1])
            ]
            for (angle_a, range_a), (angle_b, range_b) in zip(
                wrapped_rays, wrapped_rays[1:]
            ):
                gap_deg = angle_b - angle_a
                if gap_deg <= 0.0 or gap_deg > 5.0:
                    continue
                clear_range_m = min(range_a, range_b) - self.resolution_m * 1.5
                if clear_range_m <= self.lidar_service.min_valid_distance_mm / 1000.0:
                    continue
                target_step_deg = math.degrees(
                    math.atan2(self.resolution_m * 0.75, clear_range_m)
                )
                target_step_deg = max(0.35, min(1.0, target_step_deg))
                subdivisions = min(12, int(math.ceil(gap_deg / target_step_deg)))
                for subdivision in range(1, subdivisions):
                    fraction = subdivision / subdivisions
                    angle_deg = (angle_a + gap_deg * fraction) % 360.0
                    angle_rad = math.radians(angle_deg)
                    lx = clear_range_m * math.sin(angle_rad)
                    ly = clear_range_m * math.cos(angle_rad)
                    wx = robot_x + lx * cos_p + ly * sin_p
                    wy = robot_y - lx * sin_p + ly * cos_p
                    target_gx, target_gy = self.world_to_grid(wx, wy)
                    trace_free_ray(
                        target_gx,
                        target_gy,
                        include_endpoint=True,
                        stop_at_observed_wall=True,
                    )

        # Preserve no-return samples as conservative free-space evidence. The
        # old parser discarded them completely, leaving open doorways black
        # (unknown); the frontier planner then had nowhere legal to drive even
        # though the live scan correctly showed a clear direction.
        for angle_deg in clear_ray_angles_deg or []:
            angle_rad = math.radians(angle_deg)
            lx = self.no_return_clear_range_m * math.sin(angle_rad)
            ly = self.no_return_clear_range_m * math.cos(angle_rad)
            wx = robot_x + lx * cos_p + ly * sin_p
            wy = robot_y - lx * sin_p + ly * cos_p
            gx1, gy1 = self.world_to_grid(wx, wy)

            trace_free_ray(
                gx1,
                gy1,
                include_endpoint=True,
                stop_at_observed_wall=True,
            )

        # Update display grid matrix from log-odds values
        # -1 = unknown (|log_odds| < 0.25)
        # 0 = free (log_odds < -0.25)
        # 100 = occupied (log_odds > 0.6)
        previous_free = self._grid == 0
        previous_occupied = self._grid == 100
        free_mask = (self._log_odds < -0.25) | (
            previous_free & (self._log_odds < 0.10)
        )
        occ_mask = (self._log_odds > 0.6) | (
            previous_occupied & (self._log_odds > 0.25)
        )
        # Occupied evidence always wins if hysteresis bands overlap.
        free_mask &= ~occ_mask
        unknown_mask = ~free_mask & ~occ_mask

        self._grid[free_mask] = 0
        self._grid[occ_mask] = 100
        self._grid[unknown_mask] = -1

        return hit_world_coords

    # ------------------------------------------------------------------
    # Scan Processing Loop
    # ------------------------------------------------------------------

    def _handle_lidar_scan(self, scan_data: LidarScanData) -> None:
        """Callback invoked whenever RPLIDAR C1 delivers a 360-degree sweep."""
        # Forward to original GUI callback if present
        if self._original_lidar_callback:
            try:
                self._original_lidar_callback(scan_data)
            except Exception:
                pass

        if not self.is_mapping or not (
            scan_data.points or scan_data.clear_ray_angles_deg
        ):
            return

        with self._lock:
            # 1. Update pose via scan matching
            new_pose = self._scan_match(scan_data.points)
            self.pose = new_pose

            # Record trajectory breadcrumbs if moved > 5cm or rotated > 5°
            last_x, last_y = self.trajectory[-1]
            dist_moved = math.hypot(new_pose.x_m - last_x, new_pose.y_m - last_y)
            if dist_moved >= 0.05:
                self.trajectory.append((round(new_pose.x_m, 3), round(new_pose.y_m, 3)))
                if len(self.trajectory) > 2000:
                    self.trajectory.pop(0)

            # 2. Raycast laser rays into occupancy grid
            hits = self._raycast_update(
                new_pose.x_m,
                new_pose.y_m,
                scan_data.points,
                scan_data.clear_ray_angles_deg,
            )
            self._latest_hits = hits

            explored_cells = int(np.count_nonzero(self._grid != -1))

            snapshot = MappingSnapshot(
                pose=self.pose,
                trajectory=list(self.trajectory),
                laser_scan=hits,
                width=self.width,
                height=self.height,
                resolution_cm=self.resolution_cm,
                origin_x_m=self.origin_x_m,
                origin_y_m=self.origin_y_m,
                is_mapping=self.is_mapping,
                map_name=self.map_name,
                active_map_id=self.active_map_id,
                total_explored_cells=explored_cells,
                planned_path=list(self.navigator.current_path),
                target_frontier=self.navigator.target_frontier,
            )

        if self.on_snapshot:
            try:
                self.on_snapshot(snapshot)
            except Exception as e:
                logger.warning("Error dispatching mapping snapshot: %s", e)

    # ------------------------------------------------------------------
    # Data Export & Compression
    # ------------------------------------------------------------------

    def get_compressed_grid(self) -> bytes:
        """Compress the 2D int8 grid array using zlib."""
        with self._lock:
            return zlib.compress(self._grid.tobytes(), level=6)

    def get_grid_copy(self) -> np.ndarray:
        """Return a copy of the current 2D int8 grid array."""
        with self._lock:
            return self._grid.copy()

    def localization_status(self) -> Dict[str, Any]:
        """Return compact scan-matching confidence diagnostics."""
        return {
            "correction_accepted": self.last_scan_match_accepted,
            "reason": self.last_scan_match_reason,
            "score": self.last_scan_match_score,
            "matched_points": self.last_scan_match_points,
        }

    def get_snapshot(self) -> MappingSnapshot:
        """Return the current mapping snapshot for API consumers."""
        with self._lock:
            explored_cells = int(np.count_nonzero(self._grid != -1))
            return MappingSnapshot(
                pose=self.pose,
                trajectory=list(self.trajectory),
                laser_scan=list(self._latest_hits),
                width=self.width,
                height=self.height,
                resolution_cm=self.resolution_cm,
                origin_x_m=self.origin_x_m,
                origin_y_m=self.origin_y_m,
                is_mapping=self.is_mapping,
                map_name=self.map_name,
                active_map_id=self.active_map_id,
                total_explored_cells=explored_cells,
                planned_path=list(self.navigator.current_path),
                target_frontier=self.navigator.target_frontier,
            )

    # ------------------------------------------------------------------
    # SQLite Map Persistence
    # ------------------------------------------------------------------

    def save_current_map(self, name: Optional[str] = None) -> MapModel:
        """Save the current occupancy grid to SQLite."""
        with self._lock:
            if name:
                self.map_name = name
            compressed_data = zlib.compress(self._grid.tobytes(), level=6)

            map_obj = create_map(
                name=self.map_name,
                grid_data=compressed_data,
                width=self.width,
                height=self.height,
                resolution_cm=self.resolution_cm,
                origin_x_m=self.origin_x_m,
                origin_y_m=self.origin_y_m,
                is_active=True,
            )
            self.active_map_id = map_obj.id
            logger.info("Saved map '%s' (ID: %d) to SQLite.", self.map_name, map_obj.id)
            return map_obj

    def load_map(self, map_id: int) -> bool:
        """Load an existing occupancy grid map from SQLite by ID."""
        map_obj = get_map(map_id)
        if not map_obj:
            return False

        with self._lock:
            decompressed = zlib.decompress(map_obj.grid_data)
            loaded_grid = np.frombuffer(decompressed, dtype=np.int8).reshape(
                (map_obj.height, map_obj.width)
            )

            self.width = map_obj.width
            self.height = map_obj.height
            self.resolution_cm = map_obj.resolution_cm
            self.resolution_m = map_obj.resolution_cm / 100.0
            self.origin_x_m = map_obj.origin_x_m
            self.origin_y_m = map_obj.origin_y_m
            self.map_name = map_obj.name
            self.active_map_id = map_obj.id

            self._grid = loaded_grid.copy()
            # Re-allocate log-odds matrix matching loaded map shape
            self._log_odds = np.zeros((self.height, self.width), dtype=np.float32)
            self._log_odds[self._grid == 0] = -2.0
            self._log_odds[self._grid == 100] = 3.0

            self.pose = RobotPose(0.0, 0.0, 0.0)
            self.trajectory = [(0.0, 0.0)]
            self._latest_hits.clear()

            set_active_map(map_id)

        logger.info("Loaded map '%s' (ID: %d) into SLAM engine.", map_obj.name, map_id)
        return True

    def _load_active_map_from_db(self) -> None:
        """Check SQLite for an active map and load it on startup."""
        active = get_active_map()
        if active:
            self.load_map(active.id)


# ---------------------------------------------------------------------------
# Global Singleton Accessor
# ---------------------------------------------------------------------------
_SHARED_MAPPING_SERVICE: Optional[MappingService] = None


def get_mapping_service() -> MappingService:
    """Get or instantiate the global shared MappingService singleton."""
    global _SHARED_MAPPING_SERVICE
    if _SHARED_MAPPING_SERVICE is None:
        _SHARED_MAPPING_SERVICE = MappingService()
    return _SHARED_MAPPING_SERVICE
