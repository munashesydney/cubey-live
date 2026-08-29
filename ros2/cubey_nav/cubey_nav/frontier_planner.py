"""Pure occupancy-grid frontier selection used by the ROS exploration node."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class GridMap:
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    data: Sequence[int]

    def value(self, x: int, y: int) -> int:
        return int(self.data[y * self.width + x])

    def world(self, x: int, y: int) -> tuple[float, float]:
        return (
            self.origin_x + (x + 0.5) * self.resolution,
            self.origin_y + (y + 0.5) * self.resolution,
        )

    def cell(self, world_x: float, world_y: float) -> tuple[int, int]:
        return (
            int(math.floor((world_x - self.origin_x) / self.resolution)),
            int(math.floor((world_y - self.origin_y) / self.resolution)),
        )


@dataclass(frozen=True)
class FrontierGoal:
    x: float
    y: float
    cell_x: int
    cell_y: int
    cluster_size: int
    score: float


def select_frontier_goal(
    grid: GridMap,
    robot_x: float,
    robot_y: float,
    *,
    excluded_world: Iterable[tuple[float, float]] = (),
    excluded_radius_m: float = 0.55,
    minimum_cluster_cells: int = 8,
    clearance_m: float = 0.24,
    standoff_m: float = 0.28,
) -> FrontierGoal | None:
    """Return a known-free, footprint-clear goal just inside the best frontier."""

    if (
        grid.width < 3
        or grid.height < 3
        or grid.resolution <= 0.0
        or len(grid.data) != grid.width * grid.height
    ):
        return None

    frontiers: set[tuple[int, int]] = set()
    for y in range(1, grid.height - 1):
        row = y * grid.width
        for x in range(1, grid.width - 1):
            value = int(grid.data[row + x])
            if value < 0 or value > 25:
                continue
            if any(
                grid.value(nx, ny) < 0
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            ):
                frontiers.add((x, y))

    clusters: list[list[tuple[int, int]]] = []
    remaining = set(frontiers)
    while remaining:
        seed = remaining.pop()
        cluster = [seed]
        queue = deque([seed])
        while queue:
            x, y = queue.popleft()
            for ny in range(y - 1, y + 2):
                for nx in range(x - 1, x + 2):
                    neighbor = (nx, ny)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
                        cluster.append(neighbor)
        if len(cluster) >= minimum_cluster_cells:
            clusters.append(cluster)

    excluded = tuple(excluded_world)
    best: FrontierGoal | None = None
    for cluster in clusters:
        centroid_x = sum(cell[0] for cell in cluster) / len(cluster)
        centroid_y = sum(cell[1] for cell in cluster) / len(cluster)
        # A frontier cluster can wrap around an explored island; its arithmetic
        # centroid may then lie in the middle of known space. Anchor the goal
        # to a real frontier member closest to that centroid.
        frontier_cell_x, frontier_cell_y = min(
            cluster,
            key=lambda cell: (cell[0] - centroid_x) ** 2
            + (cell[1] - centroid_y) ** 2,
        )
        frontier_world_x, frontier_world_y = grid.world(
            frontier_cell_x, frontier_cell_y
        )

        toward_robot_x = robot_x - frontier_world_x
        toward_robot_y = robot_y - frontier_world_y
        magnitude = math.hypot(toward_robot_x, toward_robot_y)
        if magnitude > 1.0e-6:
            target_world_x = frontier_world_x + toward_robot_x / magnitude * standoff_m
            target_world_y = frontier_world_y + toward_robot_y / magnitude * standoff_m
        else:
            target_world_x = frontier_world_x
            target_world_y = frontier_world_y

        preferred_x, preferred_y = grid.cell(target_world_x, target_world_y)
        candidate = _nearest_clear_cell(
            grid,
            preferred_x,
            preferred_y,
            clearance_m=clearance_m,
            search_radius_m=max(0.25, standoff_m),
        )
        if candidate is None:
            continue
        cell_x, cell_y = candidate
        goal_x, goal_y = grid.world(cell_x, cell_y)
        if any(
            math.hypot(goal_x - failed_x, goal_y - failed_y) < excluded_radius_m
            for failed_x, failed_y in excluded
        ):
            continue

        distance = math.hypot(goal_x - robot_x, goal_y - robot_y)
        if distance < 0.15:
            continue
        information_gain = len(cluster) * grid.resolution
        score = information_gain * 2.0 - distance
        goal = FrontierGoal(
            x=goal_x,
            y=goal_y,
            cell_x=cell_x,
            cell_y=cell_y,
            cluster_size=len(cluster),
            score=score,
        )
        if best is None or goal.score > best.score:
            best = goal
    return best


def _nearest_clear_cell(
    grid: GridMap,
    center_x: int,
    center_y: int,
    *,
    clearance_m: float,
    search_radius_m: float,
) -> tuple[int, int] | None:
    search_cells = max(1, int(math.ceil(search_radius_m / grid.resolution)))
    candidates: list[tuple[float, int, int]] = []
    for y in range(center_y - search_cells, center_y + search_cells + 1):
        for x in range(center_x - search_cells, center_x + search_cells + 1):
            if not (0 <= x < grid.width and 0 <= y < grid.height):
                continue
            distance_sq = (x - center_x) ** 2 + (y - center_y) ** 2
            candidates.append((distance_sq, x, y))
    candidates.sort(key=lambda item: item[0])
    for _, x, y in candidates:
        if _is_known_free_with_clearance(grid, x, y, clearance_m):
            return x, y
    return None


def _is_known_free_with_clearance(
    grid: GridMap,
    center_x: int,
    center_y: int,
    clearance_m: float,
) -> bool:
    radius_cells = max(1, int(math.ceil(clearance_m / grid.resolution)))
    radius_sq = (clearance_m / grid.resolution) ** 2
    for y in range(center_y - radius_cells, center_y + radius_cells + 1):
        for x in range(center_x - radius_cells, center_x + radius_cells + 1):
            if (x - center_x) ** 2 + (y - center_y) ** 2 > radius_sq:
                continue
            if not (0 <= x < grid.width and 0 <= y < grid.height):
                return False
            value = grid.value(x, y)
            if value < 0 or value > 25:
                return False
    return True
