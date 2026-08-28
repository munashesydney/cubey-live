"""
A* Path Planner with Obstacle Inflation and Waypoint Smoothing.

Generates safe, collision-free waypoint routes through 2D occupancy grids,
dilating walls and obstacles by the robot's physical radius to prevent collisions.
"""

import heapq
import math
from typing import List, Optional, Set, Tuple

import numpy as np


class PathPlanner:
    """Computes collision-free A* paths on 2D occupancy grids with obstacle inflation."""

    def __init__(
        self,
        robot_radius_m: float = 0.20,       # 20cm robot radius
        safety_margin_m: float = 0.05,      # 5cm additional clearance buffer
    ):
        self.total_inflation_m = robot_radius_m + safety_margin_m

    def inflate_obstacles(self, grid: np.ndarray, resolution_m: float) -> np.ndarray:
        """
        Create a boolean obstacle mask where True indicates walls or cells within
        the robot's inflation safety radius.
        """
        inflation_cells = max(1, int(math.ceil(self.total_inflation_m / resolution_m)))
        height, width = grid.shape
        is_occupied = (grid == 100)

        # Fast Manhattan / Euclidean distance dilation
        inflated = is_occupied.copy()
        if not np.any(is_occupied):
            return inflated

        # Coordinates of occupied cells
        occupied_indices = np.argwhere(is_occupied)
        for y, x in occupied_indices:
            y_min = max(0, y - inflation_cells)
            y_max = min(height, y + inflation_cells + 1)
            x_min = max(0, x - inflation_cells)
            x_max = min(width, x + inflation_cells + 1)

            # Circular mask check
            for cy in range(y_min, y_max):
                for cx in range(x_min, x_max):
                    if (cx - x) ** 2 + (cy - y) ** 2 <= inflation_cells ** 2:
                        inflated[cy, cx] = True

        return inflated

    def plan_path(
        self,
        grid: np.ndarray,
        resolution_m: float,
        origin_x_m: float,
        origin_y_m: float,
        start_world: Tuple[float, float],
        goal_world: Tuple[float, float],
    ) -> Optional[List[Tuple[float, float]]]:
        """
        Compute smoothed collision-free world waypoints from start_world to goal_world.
        Returns list of (x, y) coordinates in meters, or None if unreachable.
        """
        height, width = grid.shape

        # Convert world coordinates to grid indices
        start_gx = int((start_world[0] - origin_x_m) / resolution_m)
        start_gy = int((start_world[1] - origin_y_m) / resolution_m)

        goal_gx = int((goal_world[0] - origin_x_m) / resolution_m)
        goal_gy = int((goal_world[1] - origin_y_m) / resolution_m)

        # Clamp into bounds
        start_gx = max(0, min(width - 1, start_gx))
        start_gy = max(0, min(height - 1, start_gy))
        goal_gx = max(0, min(width - 1, goal_gx))
        goal_gy = max(0, min(height - 1, goal_gy))

        inflated_mask = self.inflate_obstacles(grid, resolution_m)

        # If goal is inside an inflated obstacle, find closest free cell
        if inflated_mask[goal_gy, goal_gx]:
            closest_free = self._find_nearest_free_cell(inflated_mask, goal_gx, goal_gy)
            if not closest_free:
                return None
            goal_gx, goal_gy = closest_free

        # 8-connected movement steps: (dx, dy, cost)
        MOVES = [
            (1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
            (1, 1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (-1, -1, 1.414),
        ]

        # A* Search
        # Priority queue stores (f_score, g_score, (gx, gy))
        open_set = []
        heapq.heappush(open_set, (0.0, 0.0, (start_gx, start_gy)))

        came_from = {}
        g_score = {(start_gx, start_gy): 0.0}

        def heuristic(x: int, y: int) -> float:
            dx = abs(x - goal_gx)
            dy = abs(y - goal_gy)
            return (dx + dy) + (1.414 - 2.0) * min(dx, dy)

        found = False

        while open_set:
            _, current_g, current = heapq.heappop(open_set)
            cx, cy = current

            if current == (goal_gx, goal_gy):
                found = True
                break

            if current_g > g_score.get(current, float("inf")):
                continue

            for dx, dy, move_cost in MOVES:
                nx, ny = cx + dx, cy + dy

                # Bounds check
                if not (0 <= nx < width and 0 <= ny < height):
                    continue

                # Collision / Unexplored check: must not be in inflated obstacle and must be free (0)
                if inflated_mask[ny, nx] or grid[ny, nx] != 0:
                    continue

                # Diagonal corner-cutting check
                if dx != 0 and dy != 0:
                    if inflated_mask[cy, nx] or inflated_mask[ny, cx]:
                        continue

                tentative_g = current_g + move_cost
                neighbor = (nx, ny)

                if tentative_g < g_score.get(neighbor, float("inf")):
                    g_score[neighbor] = tentative_g
                    came_from[neighbor] = current
                    f_score = tentative_g + heuristic(nx, ny)
                    heapq.heappush(open_set, (f_score, tentative_g, neighbor))

        if not found:
            return None

        # Reconstruct path
        grid_path = []
        curr = (goal_gx, goal_gy)
        while curr in came_from:
            grid_path.append(curr)
            curr = came_from[curr]
        grid_path.append((start_gx, start_gy))
        grid_path.reverse()

        # Smooth path using Line-of-Sight simplification
        smoothed_grid_path = self._smooth_path(grid_path, inflated_mask)

        # Convert to real-world metric coordinates
        world_path = [
            (
                round((gx + 0.5) * resolution_m + origin_x_m, 3),
                round((gy + 0.5) * resolution_m + origin_y_m, 3),
            )
            for gx, gy in smoothed_grid_path
        ]

        return world_path

    def _find_nearest_free_cell(self, mask: np.ndarray, gx: int, gy: int) -> Optional[Tuple[int, int]]:
        """Find the closest un-inflated cell to a target."""
        height, width = mask.shape
        for r in range(1, 15):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = gx + dx, gy + dy
                    if 0 <= nx < width and 0 <= ny < height:
                        if not mask[ny, nx]:
                            return nx, ny
        return None

    def _has_line_of_sight(self, p1: Tuple[int, int], p2: Tuple[int, int], mask: np.ndarray) -> bool:
        """Bresenham line check for unobstructed line-of-sight between two grid cells."""
        x0, y0 = p1
        x1, y1 = p2
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        curr_x, curr_y = x0, y0
        while curr_x != x1 or curr_y != y1:
            if mask[curr_y, curr_x]:
                return False
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                curr_x += sx
            if e2 < dx:
                err += dx
                curr_y += sy

        return not mask[y1, x1]

    def _smooth_path(self, path: List[Tuple[int, int]], mask: np.ndarray) -> List[Tuple[int, int]]:
        """Simplify dense grid step paths into sparse straight-line waypoints."""
        if len(path) <= 2:
            return path

        smoothed = [path[0]]
        curr_idx = 0

        while curr_idx < len(path) - 1:
            # Find the furthest reachable waypoint in direct line-of-sight
            next_idx = len(path) - 1
            while next_idx > curr_idx + 1:
                if self._has_line_of_sight(path[curr_idx], path[next_idx], mask):
                    break
                next_idx -= 1

            smoothed.append(path[next_idx])
            curr_idx = next_idx

        return smoothed
