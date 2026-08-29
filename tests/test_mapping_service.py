"""
Unit tests for MappingService: coordinate transformation, occupancy grid updates,
LiDAR scan matching, raycasting, and map serialization.
"""

import time
import unittest
import numpy as np

from src.services.lidar_service import LidarPoint, LidarScanData, LidarService
from src.services.mapping_service import MappingService, RobotPose


class MappingServiceTests(unittest.TestCase):
    """Test 2D SLAM occupancy grid engine."""

    def setUp(self):
        self.mock_lidar = LidarService(default_port="MOCK_SIMULATOR")
        self.mapping_svc = MappingService(
            width=100,
            height=100,
            resolution_m=0.05,
            lidar_service=self.mock_lidar,
            autonomy_enabled=True,
        )

    def tearDown(self):
        self.mock_lidar.disconnect()

    def test_coordinate_transforms(self):
        # Origin is at (-2.5m, -2.5m) for 100x100 @ 0.05m
        # (0, 0) in world should map to center cell (50, 50)
        gx, gy = self.mapping_svc.world_to_grid(0.0, 0.0)
        self.assertEqual(gx, 50)
        self.assertEqual(gy, 50)

        # Convert back
        wx, wy = self.mapping_svc.grid_to_world(gx, gy)
        self.assertAlmostEqual(wx, 0.025, places=2)
        self.assertAlmostEqual(wy, 0.025, places=2)

    def test_mapping_start_pause_reset(self):
        self.assertFalse(self.mapping_svc.is_mapping)
        self.mapping_svc.start_mapping()
        self.assertTrue(self.mapping_svc.is_mapping)

        self.mapping_svc.pause_mapping()
        self.assertFalse(self.mapping_svc.is_mapping)

        self.mapping_svc.reset_map()
        self.assertEqual(self.mapping_svc.pose.x_m, 0.0)
        self.assertEqual(self.mapping_svc.pose.y_m, 0.0)

    def test_mapping_only_mode_does_not_start_autonomy(self):
        mapping_only = MappingService(
            width=100,
            height=100,
            resolution_m=0.05,
            lidar_service=self.mock_lidar,
            autonomy_enabled=False,
        )

        autonomous_started = mapping_only.start_mapping()

        self.assertFalse(autonomous_started)
        self.assertTrue(mapping_only.is_mapping)
        self.assertFalse(mapping_only.navigator.is_active)
        mapping_only.pause_mapping()

    def test_raycasting_marks_free_and_occupied(self):
        # Single point 1.0 meter straight ahead (North: angle=0°)
        scan_points = [
            LidarPoint(angle_deg=0.0, distance_mm=1000.0, quality=60)
        ]

        hits = self.mapping_svc._raycast_update(0.0, 0.0, scan_points)
        self.assertEqual(len(hits), 1)

        # Robot cell (50, 50) -> target cell at 1.0m north = (50, 70)
        # Check that target endpoint is marked occupied (100)
        gx_hit, gy_hit = self.mapping_svc.world_to_grid(0.0, 1.0)
        self.assertEqual(self.mapping_svc._grid[gy_hit, gx_hit], 100)

        # Check that halfway cell (0.0, 0.5m) is marked free (0)
        gx_mid, gy_mid = self.mapping_svc.world_to_grid(0.0, 0.5)
        self.assertEqual(self.mapping_svc._grid[gy_mid, gx_mid], 0)

    def test_compression_and_persistence(self):
        compressed = self.mapping_svc.get_compressed_grid()
        self.assertIsInstance(compressed, bytes)
        self.assertGreater(len(compressed), 0)

        # Save to SQLite
        map_model = self.mapping_svc.save_current_map("Test Map Save")
        self.assertIsNotNone(map_model.id)
        self.assertEqual(self.mapping_svc.map_name, "Test Map Save")

        # Load back
        loaded = self.mapping_svc.load_map(map_model.id)
        self.assertTrue(loaded)
        self.assertEqual(self.mapping_svc.map_name, "Test Map Save")


if __name__ == "__main__":
    unittest.main()
