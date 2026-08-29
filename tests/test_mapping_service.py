"""
Unit tests for MappingService: coordinate transformation, occupancy grid updates,
LiDAR scan matching, raycasting, and map serialization.
"""

import math
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

    def test_scan_matching_holds_pose_when_scan_has_no_map_correspondence(self):
        self.mapping_svc._grid.fill(-1)
        self.mapping_svc._grid[5, 5:30] = 100
        self.mapping_svc.pose = RobotPose(0.0, 0.0, 0.0)
        points = [
            LidarPoint(
                angle_deg=float(index * 20),
                distance_mm=800.0 + index * 13.0,
                quality=60,
            )
            for index in range(18)
        ]

        for _ in range(5):
            self.mapping_svc.pose = self.mapping_svc._scan_match(points)

        self.assertAlmostEqual(self.mapping_svc.pose.x_m, 0.0)
        self.assertAlmostEqual(self.mapping_svc.pose.y_m, 0.0)
        self.assertAlmostEqual(self.mapping_svc.pose.theta_deg, 0.0)
        self.assertFalse(self.mapping_svc.last_scan_match_accepted)
        self.assertEqual(
            self.mapping_svc.last_scan_match_reason,
            "insufficient_map_correspondence",
        )

    def test_scan_matching_accepts_supported_motion_correction(self):
        self.mapping_svc._grid.fill(-1)
        self.mapping_svc.pose = RobotPose(0.0, 0.0, 0.0)
        self.mapping_svc.navigator._last_command = "forward"
        true_x, true_y, true_theta = 0.0, 0.06, 4.0
        points = []
        for index in range(36):
            angle = float(index * 10)
            distance_m = 1.1 + 0.22 * math.sin(math.radians(angle * 3))
            point = LidarPoint(
                angle_deg=angle,
                distance_mm=distance_m * 1000.0,
                quality=60,
            )
            points.append(point)
            theta_rad = math.radians(true_theta)
            wx = true_x + point.x_m * math.cos(theta_rad) + point.y_m * math.sin(theta_rad)
            wy = true_y - point.x_m * math.sin(theta_rad) + point.y_m * math.cos(theta_rad)
            gx, gy = self.mapping_svc.world_to_grid(wx, wy)
            self.mapping_svc._grid[gy, gx] = 100

        matched_pose = self.mapping_svc._scan_match(points)

        self.assertAlmostEqual(matched_pose.y_m, true_y, delta=0.021)
        self.assertAlmostEqual(matched_pose.theta_deg, true_theta, delta=2.01)
        self.assertTrue(self.mapping_svc.last_scan_match_accepted)

    def test_raycasting_ignores_robot_chassis_reflection(self):
        reflection = LidarPoint(angle_deg=351.4, distance_mm=56.0, quality=60)

        hits = self.mapping_svc._raycast_update(0.0, 0.0, [reflection])

        self.assertEqual(hits, [])
        self.assertEqual(int(np.count_nonzero(self.mapping_svc._grid == 100)), 0)

    def test_no_return_ray_marks_a_short_open_corridor_free(self):
        hits = self.mapping_svc._raycast_update(
            0.0, 0.0, [], clear_ray_angles_deg=[0.0]
        )

        gx_near, gy_near = self.mapping_svc.world_to_grid(0.0, 0.5)
        gx_far, gy_far = self.mapping_svc.world_to_grid(0.0, 1.0)
        self.assertEqual(hits, [])
        self.assertEqual(self.mapping_svc._grid[gy_near, gx_near], 0)
        self.assertEqual(self.mapping_svc._grid[gy_far, gx_far], -1)
        self.assertEqual(int(np.count_nonzero(self.mapping_svc._grid == 100)), 0)

    def test_no_return_ray_does_not_erase_an_observed_wall(self):
        gx_wall, gy_wall = self.mapping_svc.world_to_grid(0.0, 0.5)
        self.mapping_svc._log_odds[gy_wall, gx_wall] = 2.0
        self.mapping_svc._grid[gy_wall, gx_wall] = 100

        self.mapping_svc._raycast_update(
            0.0, 0.0, [], clear_ray_angles_deg=[0.0]
        )

        self.assertEqual(self.mapping_svc._grid[gy_wall, gx_wall], 100)

    def test_observed_wall_survives_several_crossing_free_rays(self):
        wall = LidarPoint(angle_deg=0.0, distance_mm=500.0, quality=60)
        self.mapping_svc._raycast_update(0.0, 0.0, [wall])
        gx_wall, gy_wall = self.mapping_svc.world_to_grid(0.0, 0.5)

        farther_return = LidarPoint(
            angle_deg=0.0, distance_mm=1000.0, quality=60
        )
        for _ in range(4):
            self.mapping_svc._raycast_update(0.0, 0.0, [farther_return])

        self.assertEqual(self.mapping_svc._grid[gy_wall, gx_wall], 100)

    def test_nearby_scan_samples_fill_free_space_between_rays(self):
        points = [
            LidarPoint(angle_deg=0.0, distance_mm=2000.0, quality=60),
            LidarPoint(angle_deg=4.0, distance_mm=2000.0, quality=60),
        ]

        self.mapping_svc._raycast_update(0.0, 0.0, points)

        angle_rad = math.radians(2.0)
        gx_mid, gy_mid = self.mapping_svc.world_to_grid(
            1.5 * math.sin(angle_rad),
            1.5 * math.cos(angle_rad),
        )
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
