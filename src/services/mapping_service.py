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
    timestamp: float = field(default_factory=time.time)


class MappingService:
    """
    Coordinates 2D SLAM occupancy grid mapping, LiDAR scan-matching pose estimation,
    and map persistence.
    """

    def __init__(
        self,
        width: int = 400,
        height: int = 400,
        resolution_m: float = 0.05,  # 5cm per pixel -> 20m x 20m map
        lidar_service=None,
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
    # Mapping Controls
    # ------------------------------------------------------------------

    def start_mapping(self) -> None:
        """Enable active SLAM map updates from incoming LiDAR scans."""
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

        logger.info("2D House Mapping session started.")

    def pause_mapping(self) -> None:
        """Pause grid updates while keeping current map state."""
        with self._lock:
            self.is_mapping = False
        logger.info("2D House Mapping session paused.")

    def reset_map(self) -> None:
        """Clear grid back to unexplored space and reset robot pose."""
        with self._lock:
            self._log_odds.fill(0.0)
            self._grid.fill(-1)
            self.pose = RobotPose(0.0, 0.0, 0.0)
            self.trajectory = [(0.0, 0.0)]
            self._latest_hits.clear()
            self.active_map_id = None
            self.map_name = "Live Floorplan"
        logger.info("Occupancy grid map reset.")

    # LiDAR mounting position relative to robot geometric center:
    # Centered left/right (X=0.0m), shifted 35mm rearward (Y=-0.035m)
    LIDAR_OFFSET_X = 0.0
    LIDAR_OFFSET_Y = -0.035

    # ------------------------------------------------------------------
    # Robust Multi-Resolution Scan Matching & Pose Tracking
    # ------------------------------------------------------------------

    def _scan_match(self, scan_points: List[LidarPoint]) -> RobotPose:
        """
        Robust multi-resolution scan matcher that correlates 360° LiDAR sweeps
        against the global occupancy grid, compensating for rotation and sensor offset.
        """
        # Filter points: remove chassis reflections (<140mm) and noisy far points (>12m)
        valid = [p for p in scan_points if 140 <= p.distance_mm <= 12000 and p.quality > 10]
        if len(valid) < 25 or np.count_nonzero(self._grid == 100) < 30:
            return self.pose

        # Downsample to ~90 points for fast vectorized matching
        if len(valid) > 90:
            step = len(valid) // 90
            valid = valid[::step]

        # Extract local points in robot frame accounting for 35mm sensor offset
        angles_rad = np.array([math.radians(p.angle_deg) for p in valid], dtype=np.float32)
        dists_m = np.array([p.distance_mm / 1000.0 for p in valid], dtype=np.float32)

        local_x = dists_m * np.sin(angles_rad) + self.LIDAR_OFFSET_X
        local_y = dists_m * np.cos(angles_rad) + self.LIDAR_OFFSET_Y

        best_score = -999999.0
        best_x = self.pose.x_m
        best_y = self.pose.y_m
        best_th = self.pose.theta_deg

        # --- PASS 1: Coarse Multi-Angle Search (captures turns up to +-25 deg) ---
        coarse_dth = [-24.0, -18.0, -12.0, -6.0, 0.0, 6.0, 12.0, 18.0, 24.0]
        coarse_dx = [-0.10, -0.05, 0.0, 0.05, 0.10]
        coarse_dy = [-0.10, -0.05, 0.0, 0.05, 0.10]

        for dth in coarse_dth:
            th = self.pose.theta_deg + dth
            rad = math.radians(th)
            cos_th = math.cos(rad)
            sin_th = math.sin(rad)

            # Rotated points
            rot_x = local_x * cos_th + local_y * sin_th
            rot_y = -local_x * sin_th + local_y * cos_th

            for dx in coarse_dx:
                cx = self.pose.x_m + dx
                for dy in coarse_dy:
                    cy = self.pose.y_m + dy

                    wx = cx + rot_x
                    wy = cy + rot_y

                    gx = ((wx - self.origin_x_m) / self.resolution_m).astype(np.int32)
                    gy = ((wy - self.origin_y_m) / self.resolution_m).astype(np.int32)

                    # Mask inside bounds
                    in_bounds = (gx >= 0) & (gx < self.width) & (gy >= 0) & (gy < self.height)
                    if not np.any(in_bounds):
                        continue

                    vals = self._grid[gy[in_bounds], gx[in_bounds]]
                    score = np.sum(vals == 100) * 1.5 - np.sum(vals == 0) * 0.15

                    if score > best_score:
                        best_score = score
                        best_x = cx
                        best_y = cy
                        best_th = th

        # --- PASS 2: Fine Search (+- 4 deg in 1 deg steps, +- 3 cm in 1.5 cm steps) ---
        fine_dth = [-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0]
        fine_dx = [-0.03, -0.015, 0.0, 0.015, 0.03]
        fine_dy = [-0.03, -0.015, 0.0, 0.015, 0.03]

        refined_x, refined_y, refined_th = best_x, best_y, best_th

        for dth in fine_dth:
            th = best_th + dth
            rad = math.radians(th)
            cos_th = math.cos(rad)
            sin_th = math.sin(rad)

            rot_x = local_x * cos_th + local_y * sin_th
            rot_y = -local_x * sin_th + local_y * cos_th

            for dx in fine_dx:
                cx = best_x + dx
                for dy in fine_dy:
                    cy = best_y + dy

                    wx = cx + rot_x
                    wy = cy + rot_y

                    gx = ((wx - self.origin_x_m) / self.resolution_m).astype(np.int32)
                    gy = ((wy - self.origin_y_m) / self.resolution_m).astype(np.int32)

                    in_bounds = (gx >= 0) & (gx < self.width) & (gy >= 0) & (gy < self.height)
                    if not np.any(in_bounds):
                        continue

                    vals = self._grid[gy[in_bounds], gx[in_bounds]]
                    score = np.sum(vals == 100) * 1.5 - np.sum(vals == 0) * 0.15

                    if score > best_score:
                        best_score = score
                        refined_x = cx
                        refined_y = cy
                        refined_th = th

        # Normalize theta to [0, 360)
        norm_theta = refined_th % 360.0

        return RobotPose(x_m=refined_x, y_m=refined_y, theta_deg=norm_theta, timestamp=time.time())

    # ------------------------------------------------------------------
    # Raycasting (Bresenham's Line Algorithm)
    # ------------------------------------------------------------------

    def _raycast_update(self, robot_x: float, robot_y: float, scan_points: List[LidarPoint]) -> List[Tuple[float, float]]:
        """
        Clears free cells along laser rays and marks solid obstacle endpoints in the grid.
        Returns list of global world (x, y) hit points.
        """
        L_OCC = 1.4    # Log-odds increase for obstacle hits
        L_FREE = -0.35 # Log-odds decrease for free space
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
            # Ignore self-reflections (< 14cm) and noise
            if dist_m < 0.14 or dist_m > 14.0:
                continue

            pt_rad = math.radians(pt.angle_deg)
            # Local sensor point
            sx = dist_m * math.sin(pt_rad)
            sy = dist_m * math.cos(pt_rad)

            # Account for 35mm rearward LiDAR mounting offset
            lx = sx + self.LIDAR_OFFSET_X
            ly = sy + self.LIDAR_OFFSET_Y

            # Transform into world coordinates
            wx = robot_x + lx * cos_p + ly * sin_p
            wy = robot_y - lx * sin_p + ly * cos_p

            hit_world_coords.append((round(wx, 3), round(wy, 3)))

            gx1, gy1 = self.world_to_grid(wx, wy)

            # Bresenham's line algorithm between (gx0, gy0) and (gx1, gy1)
            dx = abs(gx1 - gx0)
            dy = abs(gy1 - gy0)
            sx_step = 1 if gx0 < gx1 else -1
            sy_step = 1 if gy0 < gy1 else -1
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
                    curr_x += sx_step
                if e2 < dx:
                    err += dx
                    curr_y += sy_step

            # Mark endpoint as occupied if quality is good
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
