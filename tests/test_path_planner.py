"""
Unit tests for A* PathPlanner with obstacle inflation.
"""

import unittest
import numpy as np

from src.services.navigation.path_planner import PathPlanner


class PathPlannerTests(unittest.TestCase):
    """Test obstacle inflation, A* collision-free routes, and waypoint smoothing."""

    def setUp(self):
        self.planner = PathPlanner(robot_radius_m=0.10, safety_margin_m=0.05)

    def test_plan_straight_path(self):
        # 40x40 free space grid
        grid = np.zeros((40, 40), dtype=np.int8)

        path = self.planner.plan_path(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=-1.0,
            origin_y_m=-1.0,
            start_world=(0.0, 0.0),
            goal_world=(0.5, 0.5),
        )

        self.assertIsNotNone(path)
        self.assertGreaterEqual(len(path), 2)
        # Verify start and end
        self.assertAlmostEqual(path[0][0], 0.025, delta=0.05)
        self.assertAlmostEqual(path[-1][0], 0.525, delta=0.05)

    def test_plan_around_wall_obstacle(self):
        # 40x40 grid with a wall in the middle
        grid = np.zeros((40, 40), dtype=np.int8)
        grid[15:25, 20] = 100  # Vertical wall at x=20 (world x = 0.025m)

        path = self.planner.plan_path(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=-1.0,
            origin_y_m=-1.0,
            start_world=(-0.3, 0.0),
            goal_world=(0.3, 0.0),
        )

        self.assertIsNotNone(path)
        # Verify that path routes around the wall (y coordinates move away from y=0)
        y_coords = [p[1] for p in path]
        max_y_deviation = max(abs(y) for y in y_coords)
        self.assertGreater(max_y_deviation, 0.15)

    def test_smoothing_never_shortcuts_through_unknown_space(self):
        grid = np.full((12, 12), -1, dtype=np.int8)
        grid[1, 1:10] = 0
        grid[1:10, 9] = 0

        planner = PathPlanner(robot_radius_m=0.0, safety_margin_m=0.0)
        path = planner.plan_path(
            grid=grid,
            resolution_m=1.0,
            origin_x_m=0.0,
            origin_y_m=0.0,
            start_world=(1.5, 1.5),
            goal_world=(9.5, 9.5),
        )

        self.assertIsNotNone(path)
        self.assertGreater(len(path), 2)
        for start, end in zip(path, path[1:]):
            x0, y0 = int(start[0]), int(start[1])
            x1, y1 = int(end[0]), int(end[1])
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx - dy
            while True:
                self.assertEqual(grid[y0, x0], 0)
                if (x0, y0) == (x1, y1):
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy

    def test_unknown_start_is_rejected(self):
        grid = np.full((10, 10), -1, dtype=np.int8)
        grid[2:8, 2:8] = 0

        path = self.planner.plan_path(
            grid=grid,
            resolution_m=0.1,
            origin_x_m=0.0,
            origin_y_m=0.0,
            start_world=(0.15, 0.15),
            goal_world=(0.65, 0.65),
        )

        self.assertIsNone(path)
        self.assertEqual(self.planner.last_failure_reason, "start_not_known_free")

    def test_inflation_uses_metric_radius_without_cell_rounding_overreach(self):
        grid = np.zeros((25, 25), dtype=np.int8)
        grid[12, 12] = 100
        planner = PathPlanner(robot_radius_m=0.18, safety_margin_m=0.08)

        inflated = planner.inflate_obstacles(grid, resolution_m=0.05)

        self.assertTrue(inflated[12, 17])   # 25cm from obstacle
        self.assertFalse(inflated[12, 18])  # 30cm exceeds the true 26cm radius

    def test_plans_through_open_sixty_centimeter_corridor(self):
        # A 36cm robot with an 8cm margin on either side requires 52cm.
        # The old cell-rounded inflation incorrectly closed this 60cm corridor.
        grid = np.full((40, 40), -1, dtype=np.int8)
        grid[5:31, 15:26] = 0
        grid[8:31, 14] = 100
        grid[8:31, 26] = 100
        grid[30, 14:27] = 100
        planner = PathPlanner(robot_radius_m=0.18, safety_margin_m=0.08)

        path = planner.plan_path(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=0.0,
            origin_y_m=0.0,
            start_world=(1.025, 1.225),  # grid cell (20, 24)
            goal_world=(1.025, 0.425),   # grid cell (20, 8), toward opening
        )

        self.assertIsNotNone(path, planner.last_failure_reason)
        self.assertGreaterEqual(len(path), 2)

    def test_plans_path_when_start_is_in_tight_enclosure(self):
        # The 50cm opening fits a 36cm robot with the configured 4cm margin.
        grid = np.full((40, 40), -1, dtype=np.int8)
        grid[5:30, 15:25] = 0  # free interior and opening
        grid[10:30, 14] = 100  # left wall
        grid[10:30, 25] = 100  # right wall
        grid[29, 14:26] = 100  # back wall
        grid[2:10, 10:30] = 0  # open room ahead

        planner = PathPlanner(robot_radius_m=0.18, safety_margin_m=0.04)

        path = planner.plan_path(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=0.0,
            origin_y_m=0.0,
            start_world=(1.0, 1.0),   # Inside the tight box (x=20, y=20)
            goal_world=(1.0, 0.25),   # Out in the open room ahead (x=20, y=5)
        )

        self.assertIsNotNone(path, planner.last_failure_reason)
        self.assertGreaterEqual(len(path), 2)

    def test_smoothed_route_never_shortcuts_through_footprint_inflation(self):
        grid = np.zeros((31, 31), dtype=np.int8)
        grid[15, 15] = 100
        planner = PathPlanner(robot_radius_m=0.18, safety_margin_m=0.04)

        path = planner.plan_path(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=0.0,
            origin_y_m=0.0,
            start_world=(0.275, 0.775),
            goal_world=(1.275, 0.775),
        )

        self.assertIsNotNone(path, planner.last_failure_reason)
        inflated = planner.inflate_obstacles(grid, 0.05)
        for start, end in zip(path, path[1:]):
            x0, y0 = int(start[0] / 0.05), int(start[1] / 0.05)
            x1, y1 = int(end[0] / 0.05), int(end[1] / 0.05)
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx - dy
            while True:
                self.assertFalse(inflated[y0, x0])
                if (x0, y0) == (x1, y1):
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy


if __name__ == "__main__":
    unittest.main()
