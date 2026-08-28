"""
Unit tests for FrontierDetector in 2D SLAM exploration.
"""

import unittest
import numpy as np

from src.services.navigation.frontier_detector import FrontierCluster, FrontierDetector


class FrontierDetectorTests(unittest.TestCase):
    """Test frontier identification, filtering, and cluster ranking."""

    def setUp(self):
        self.detector = FrontierDetector(min_cluster_size=3, wall_clearance_cells=1)

    def test_find_frontiers_in_room(self):
        # 30x30 grid: center 10x10 is Free (0), rest is Unknown (-1)
        grid = np.full((30, 30), -1, dtype=np.int8)
        grid[10:20, 10:20] = 0

        # Run frontier detection with robot at (0, 0)
        frontiers = self.detector.find_frontiers(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=-0.75,
            origin_y_m=-0.75,
            robot_x_m=0.0,
            robot_y_m=0.0,
        )

        self.assertGreater(len(frontiers), 0)
        best = frontiers[0]
        self.assertIsInstance(best, FrontierCluster)
        self.assertGreaterEqual(best.size, 3)
        self.assertGreater(best.score, -100)

    def test_no_frontiers_when_fully_explored(self):
        # Entire grid is known free space (0) or walls (100)
        grid = np.zeros((20, 20), dtype=np.int8)
        grid[0, :] = 100
        grid[-1, :] = 100
        grid[:, 0] = 100
        grid[:, -1] = 100

        frontiers = self.detector.find_frontiers(
            grid=grid,
            resolution_m=0.05,
            origin_x_m=-0.5,
            origin_y_m=-0.5,
            robot_x_m=0.0,
            robot_y_m=0.0,
        )

        self.assertEqual(len(frontiers), 0)


if __name__ == "__main__":
    unittest.main()
