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
    ):
        self.width = width
        self.height = height
        self.resolution_m = resolution_m
        self.resolution_cm = resolution_m * 100.0

        # Origin offset so (0,0) world is centered in the grid
        self.origin_x_m = -(self.width * self.resolution_m) / 2.0
        self.origin_y_m = -(self.height * self.resolution_m) / 2.0

        self.lidar_service = lidar_service or get_lidar_service()

        # Log-odds probability grid (-5.0 = free, +5.0 = occupied, 0.0 = unknown)
        self._log_odds = np.zeros((self.height, self.width), dtype=np.float32)
        # Display grid: -1 = unknown, 0 = free, 100 = occupied
        self._grid = np.full((self.height, self.width), -1, dtype=np.int8)

        self._lock = threading.Lock()
        self.pose = RobotPose(0.0, 0.0, 0.0)
        self.trajectory: List[Tuple[float, float]] = [(0.0, 0.0)]
        self._latest_hits: List[Tuple[float, float]] = []

        self.is_mapping: bool = False
        self.active_map_id: Optional[int] = None
        self.map_name: str = "Live Floorplan"

        self.on_snapshot: Optional[Callable[[MappingSnapshot], None]] = None

        # Autonomous Frontier Navigation Manager. Motion is opt-in after
        # commissioning; mapping-only mode remains available by default.
        from src.services.navigation import AutoNavigator
        from src.config import config
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
            safety_stop_dist_mm=config.lidar_safety_distance_mm,
            max_scan_age_s=config.nav_max_scan_age_s,
            max_wheel_telemetry_age_s=config.nav_max_wheel_telemetry_age_s,
            min_scan_points=config.nav_min_scan_points,
            sensor_start_timeout_s=config.nav_sensor_start_timeout_s,
            progress_timeout_s=config.nav_progress_timeout_s,
            max_rotation_s=config.nav_max_rotation_s,
            max_recovery_attempts=config.nav_max_recovery_attempts,
            robot_length_m=config.robot_length_m,
            robot_width_m=config.robot_width_m,
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
            self.active_map_id = None
            self.map_name = "Live Floorplan"
        logger.info("Occupancy grid map reset.")

    # ------------------------------------------------------------------
    # Scan Matching & Pose Estimation (LiDAR Odometry)
    # ------------------------------------------------------------------

    def _scan_match(self, scan_points: List[LidarPoint]) -> RobotPose:
        """
        Correlates laser hits against the existing occupancy grid to refine
        robot position (X, Y, Theta).
        """
        # If map is fresh (less than 20 occupied cells), return current pose
        occupied_count = np.count_nonzero(self._grid == 100)
        if occupied_count < 20:
            return self.pose

        best_score = -1.0
        best_x = self.pose.x_m
        best_y = self.pose.y_m
        best_theta = self.pose.theta_deg

        # Search window around current pose estimate
        # Positional offsets: +- 6 cm in 2 cm steps
        # Angular offsets: +- 4 deg in 1 deg steps
        x_candidates = [self.pose.x_m + dx for dx in [-0.06, -0.03, 0.0, 0.03, 0.06]]
        y_candidates = [self.pose.y_m + dy for dy in [-0.06, -0.03, 0.0, 0.03, 0.06]]
        theta_candidates = [self.pose.theta_deg + dth for dth in [-4.0, -2.0, 0.0, 2.0, 4.0]]

        # Sample a subset of high-quality points to speed up correlation
        valid_points = [p for p in scan_points if 200 <= p.distance_mm <= 10000 and p.quality > 15]
        if len(valid_points) > 80:
            step = len(valid_points) // 80
            valid_points = valid_points[::step]

        if not valid_points:
            return self.pose

        for th in theta_candidates:
            rad = math.radians(th)
            sin_th = math.sin(rad)
            cos_th = math.cos(rad)

            for cand_x in x_candidates:
                for cand_y in y_candidates:
                    score = 0.0
                    for pt in valid_points:
                        pt_rad = math.radians(pt.angle_deg)
                        # Local point in robot frame
                        dist_m = pt.distance_mm / 1000.0
                        lx = dist_m * math.sin(pt_rad)
                        ly = dist_m * math.cos(pt_rad)

                        # Transform to candidate world frame
                        wx = cand_x + lx * cos_th + ly * sin_th
                        wy = cand_y - lx * sin_th + ly * cos_th

                        gx, gy = self.world_to_grid(wx, wy)
                        if self.is_inside_grid(gx, gy):
                            val = self._grid[gy, gx]
                            if val == 100:
                                score += 1.0
                            elif val == 0:
                                score -= 0.15

                    if score > best_score:
                        best_score = score
                        best_x = cand_x
                        best_y = cand_y
                        best_theta = th

        return RobotPose(x_m=best_x, y_m=best_y, theta_deg=best_theta, timestamp=time.time())

    # ------------------------------------------------------------------
    # Raycasting (Bresenham's Line Algorithm)
    # ------------------------------------------------------------------

    def _raycast_update(self, robot_x: float, robot_y: float, scan_points: List[LidarPoint]) -> List[Tuple[float, float]]:
        """
        Clears free cells along laser rays and marks solid obstacle endpoints in the grid.
        Returns list of global world (x, y) hit points.
        """
        L_OCC = 1.2    # Log-odds increase for obstacle hits
        L_FREE = -0.4  # Log-odds decrease for free space
        MAX_LOG_ODDS = 5.0
        MIN_LOG_ODDS = -5.0

        gx0, gy0 = self.world_to_grid(robot_x, robot_y)
        if not self.is_inside_grid(gx0, gy0):
            return []

        hit_world_coords: List[Tuple[float, float]] = []

        pose_rad = math.radians(self.pose.theta_deg)
        sin_p = math.sin(pose_rad)
        cos_p = math.cos(pose_rad)

        for pt in scan_points:
            dist_m = pt.distance_mm / 1000.0
            if dist_m < (self.lidar_service.min_valid_distance_mm / 1000.0) or dist_m > 14.0:
                continue

            pt_rad = math.radians(pt.angle_deg)
            lx = dist_m * math.sin(pt_rad)
            ly = dist_m * math.cos(pt_rad)

            # Transform into world coordinates
            wx = robot_x + lx * cos_p + ly * sin_p
            wy = robot_y - lx * sin_p + ly * cos_p

            hit_world_coords.append((round(wx, 3), round(wy, 3)))

            gx1, gy1 = self.world_to_grid(wx, wy)

            # Bresenham's line algorithm between (gx0, gy0) and (gx1, gy1)
            dx = abs(gx1 - gx0)
            dy = abs(gy1 - gy0)
            sx = 1 if gx0 < gx1 else -1
            sy = 1 if gy0 < gy1 else -1
            err = dx - dy

            curr_x, curr_y = gx0, gy0

            # Trace free-space along ray
            while curr_x != gx1 or curr_y != gy1:
                if self.is_inside_grid(curr_x, curr_y):
                    self._log_odds[curr_y, curr_x] = max(
                        MIN_LOG_ODDS, self._log_odds[curr_y, curr_x] + L_FREE
                    )
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    curr_x += sx
                if e2 < dx:
                    err += dx
                    curr_y += sy

            # Mark endpoint as occupied
            if self.is_inside_grid(gx1, gy1) and pt.quality > 10:
                self._log_odds[gy1, gx1] = min(
                    MAX_LOG_ODDS, self._log_odds[gy1, gx1] + L_OCC
                )

        # Update display grid matrix from log-odds values
        # -1 = unknown (|log_odds| < 0.25)
        # 0 = free (log_odds < -0.25)
        # 100 = occupied (log_odds > 0.6)
        free_mask = self._log_odds < -0.25
        occ_mask = self._log_odds > 0.6
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

        if not self.is_mapping or not scan_data.points:
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
            hits = self._raycast_update(new_pose.x_m, new_pose.y_m, scan_data.points)
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
