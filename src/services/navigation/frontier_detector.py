"""
Frontier Detector for Autonomous 2D SLAM Exploration.

Identifies boundary regions where explored free space (0) meets unexplored space (-1),
clusters them into candidate exploration targets, filters out wall-adjacent boundaries,
and ranks them to guide the robot towards the nearest high-value uncharted areas.
"""

import math
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import numpy as np


@dataclass
class FrontierCluster:
    """A contiguous cluster of unexplored boundary cells representing an open frontier."""
    cells: List[Tuple[int, int]]  # Grid (gx, gy)
    centroid_grid: Tuple[int, int]
    centroid_world: Tuple[float, float]
    size: int
    distance_to_robot_m: float
    score: float = 0.0


class FrontierDetector:
    """Extracts, clusters, and ranks open exploration frontiers on an occupancy grid."""

    def __init__(
        self,
        min_cluster_size: int = 4,      # Minimum boundary cells (~20cm open gap)
        wall_clearance_cells: int = 2,  # Must be at least 2 cells away from solid walls
    ):
        self.min_cluster_size = min_cluster_size
        self.wall_clearance_cells = wall_clearance_cells

    def find_frontiers(
        self,
        grid: np.ndarray,
        resolution_m: float,
        origin_x_m: float,
        origin_y_m: float,
        robot_x_m: float,
        robot_y_m: float,
    ) -> List[FrontierCluster]:
        """
        Scan the 2D occupancy grid and return ranked frontier clusters.
        Best target candidate is at index 0.
        """
        height, width = grid.shape
        frontier_cells: Set[Tuple[int, int]] = set()

        # 8-connected neighbor offsets
        NEIGHBORS_8 = [
            (-1, -1), (-1, 0), (-1, 1),
            (0, -1),           (0, 1),
            (1, -1),  (1, 0),  (1, 1),
        ]

        # 1. Identify all valid frontier cells
        for gy in range(1, height - 1):
            for gx in range(1, width - 1):
                if grid[gy, gx] != 0:  # Must be known free space
                    continue

                # Check if bordering unknown space (-1)
                has_unknown_neighbor = False
                is_near_wall = False

                for dy, dx in NEIGHBORS_8:
                    ny, nx = gy + dy, gx + dx
                    val = grid[ny, nx]
                    if val == -1:
                        has_unknown_neighbor = True
                    elif val == 100:
                        is_near_wall = True
                        break

                if has_unknown_neighbor and not is_near_wall:
                    # Optional wall clearance check
                    if self.wall_clearance_cells > 1:
                        for cy in range(max(0, gy - self.wall_clearance_cells), min(height, gy + self.wall_clearance_cells + 1)):
                            for cx in range(max(0, gx - self.wall_clearance_cells), min(width, gx + self.wall_clearance_cells + 1)):
                                if grid[cy, cx] == 100:
                                    is_near_wall = True
                                    break
                            if is_near_wall:
                                break

                    if not is_near_wall:
                        frontier_cells.add((gx, gy))

        if not frontier_cells:
            return []

        # 2. Cluster adjacent frontier cells using BFS connected components
        visited: Set[Tuple[int, int]] = set()
        clusters: List[FrontierCluster] = []

        for start_cell in frontier_cells:
            if start_cell in visited:
                continue

            cluster_cells: List[Tuple[int, int]] = []
            queue = deque([start_cell])
            visited.add(start_cell)

            while queue:
                curr = queue.popleft()
                cluster_cells.append(curr)
                cx, cy = curr

                for dy, dx in NEIGHBORS_8:
                    neighbor = (cx + dx, cy + dy)
                    if neighbor in frontier_cells and neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            if len(cluster_cells) >= self.min_cluster_size:
                # Compute centroid
                mean_gx = int(sum(c[0] for c in cluster_cells) / len(cluster_cells))
                mean_gy = int(sum(c[1] for c in cluster_cells) / len(cluster_cells))

                # Ensure centroid is in free space, or pick closest cell in cluster
                if grid[mean_gy, mean_gx] != 0:
                    mean_gx, mean_gy = min(
                        cluster_cells,
                        key=lambda c: (c[0] - mean_gx) ** 2 + (c[1] - mean_gy) ** 2,
                    )

                world_x = (mean_gx + 0.5) * resolution_m + origin_x_m
                world_y = (mean_gy + 0.5) * resolution_m + origin_y_m

                dist_m = math.hypot(world_x - robot_x_m, world_y - robot_y_m)

                # Frontier scoring formula: reward large open boundaries, penalize travel distance
                score = (len(cluster_cells) * 1.0) - (dist_m * 1.5)

                clusters.append(
                    FrontierCluster(
                        cells=cluster_cells,
                        centroid_grid=(mean_gx, mean_gy),
                        centroid_world=(round(world_x, 3), round(world_y, 3)),
                        size=len(cluster_cells),
                        distance_to_robot_m=round(dist_m, 2),
                        score=round(score, 2),
                    )
                )

        # 3. Sort clusters by score descending
        clusters.sort(key=lambda c: c.score, reverse=True)
        return clusters
