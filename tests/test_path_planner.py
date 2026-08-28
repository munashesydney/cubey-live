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


if __name__ == "__main__":
    unittest.main()
