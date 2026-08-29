"""
Unit tests for LidarService: protocol command generation, spatial sector calculation,
360-degree mock radar generation, and lifecycle management.
"""

import math
import time
import unittest

from src.services.lidar_service import (
    LidarPoint,
    LidarScanData,
    LidarService,
    get_lidar_service,
)


class LidarServiceTests(unittest.TestCase):
    """Test LiDAR protocol, spatial calculations, and simulation mode."""

    def setUp(self):
        self.service = LidarService()
        self.service.connect(port="MOCK_SIMULATOR")

    def tearDown(self):
        self.service.disconnect()

    def test_mock_connection_lifecycle(self):
        self.assertTrue(self.service.is_connected)
        self.assertTrue(self.service.is_mock)
        self.assertTrue(self.service.is_scanning)

        # Query mock health and info
        health = self.service.get_health()
        self.assertEqual(health.get("status"), "OK")

        info = self.service.get_device_info()
        self.assertIn("RPLIDAR", info.get("model", ""))

        self.service.disconnect()
        self.assertFalse(self.service.is_connected)
        self.assertFalse(self.service.is_scanning)

    def test_singleton_accessor(self):
        svc1 = get_lidar_service()
        svc2 = get_lidar_service()
        self.assertIs(svc1, svc2)

    def test_scan_listeners_do_not_replace_primary_callback(self):
        primary = []
        secondary = []
        self.service.on_scan_data = primary.append
        self.service.add_scan_listener(secondary.append)
        scan = LidarScanData()

        self.service._notify_scan(scan)
        self.service.remove_scan_listener(secondary.append)
        self.service._notify_scan(scan)

        self.assertEqual(primary, [scan, scan])
        self.assertEqual(secondary, [scan])

    def test_lidar_point_cartesian_math(self):
        # 0 deg = North (+Y, X=0)
        p_north = LidarPoint(angle_deg=0.0, distance_mm=1000.0, quality=50)
        self.assertAlmostEqual(p_north.x_m, 0.0, places=3)
        self.assertAlmostEqual(p_north.y_m, 1.0, places=3)

        # 90 deg = East (+X, Y=0)
        p_east = LidarPoint(angle_deg=90.0, distance_mm=2000.0, quality=50)
        self.assertAlmostEqual(p_east.x_m, 2.0, places=3)
        self.assertAlmostEqual(p_east.y_m, 0.0, places=3)

        # 180 deg = South (-Y, X=0)
        p_south = LidarPoint(angle_deg=180.0, distance_mm=1500.0, quality=50)
        self.assertAlmostEqual(p_south.x_m, 0.0, places=3)
        self.assertAlmostEqual(p_south.y_m, -1.5, places=3)

        # 270 deg = West (-X, Y=0)
        p_west = LidarPoint(angle_deg=270.0, distance_mm=500.0, quality=50)
        self.assertAlmostEqual(p_west.x_m, -0.5, places=3)
        self.assertAlmostEqual(p_west.y_m, 0.0, places=3)

    def test_sector_distance_computations(self):
        test_points = [
            LidarPoint(angle_deg=0.0, distance_mm=450.0, quality=60),     # Front
            LidarPoint(angle_deg=355.0, distance_mm=420.0, quality=60),   # Front (closer)
            LidarPoint(angle_deg=90.0, distance_mm=1200.0, quality=60),   # Right (90°)
            LidarPoint(angle_deg=180.0, distance_mm=800.0, quality=60),   # Rear (180°)
            LidarPoint(angle_deg=270.0, distance_mm=300.0, quality=60),   # Left (270°)
            LidarPoint(angle_deg=260.0, distance_mm=280.0, quality=60),   # Left (closer)
        ]

        metrics = LidarService._compute_scan_metrics(
            test_points, scan_rate_hz=10.0, sample_rate_hz=5000.0
        )

        self.assertEqual(metrics.min_front_dist_mm, 420)
        self.assertEqual(metrics.min_right_dist_mm, 1200)
        self.assertEqual(metrics.min_back_dist_mm, 800)
        self.assertEqual(metrics.min_left_dist_mm, 280)
        self.assertIsNotNone(metrics.closest_point)
        self.assertEqual(metrics.closest_point.distance_mm, 280.0)

    def test_close_wall_is_not_discarded(self):
        metrics = LidarService._compute_scan_metrics(
            [LidarPoint(angle_deg=0.0, distance_mm=100.0, quality=60)],
            scan_rate_hz=10.0,
            sample_rate_hz=1000.0,
        )

        self.assertEqual(metrics.min_front_dist_mm, 100)

    def test_no_return_samples_count_as_live_scan_samples(self):
        metrics = LidarService._compute_scan_metrics(
            [LidarPoint(angle_deg=90.0, distance_mm=1000.0, quality=60)],
            scan_rate_hz=10.0,
            sample_rate_hz=1000.0,
            clear_ray_angles_deg=[0.0, 1.0, 2.0],
        )

        self.assertEqual(metrics.point_count, 4)
        self.assertEqual(metrics.clear_ray_angles_deg, [0.0, 1.0, 2.0])
        self.assertEqual(metrics.to_dict()["clear_ray_count"], 3)

    def test_scan_health_fails_closed_for_stale_or_empty_data(self):
        service = LidarService(default_port="MOCK_SIMULATOR")
        service._is_connected = True
        service._is_scanning = True
        service.latest_scan = LidarScanData(timestamp=time.time() - 1.0)

        healthy, reason = service.scan_health(max_age_s=0.2)

        self.assertFalse(healthy)
        self.assertIn("stale", reason)

    def test_scan_health_rejects_rate_incompatible_with_freshness_window(self):
        service = LidarService(default_port="MOCK_SIMULATOR")
        service._is_connected = True
        service._is_scanning = True
        service.latest_scan = LidarScanData(
            timestamp=time.time(),
            scan_rate_hz=2.5,
            point_count=100,
        )

        healthy, reason = service.scan_health(max_age_s=0.35)

        self.assertFalse(healthy)
        self.assertIn("scan_rate_low", reason)

    def test_mock_radar_stream_emission(self):
        received_scans = []
        self.service.on_scan_data = lambda scan: received_scans.append(scan)

        # Allow mock loop to emit 2-3 frames (~250ms)
        time.sleep(0.35)

        self.assertGreaterEqual(len(received_scans), 1)
        latest = received_scans[-1]
        self.assertGreater(latest.point_count, 100)
        self.assertGreater(latest.min_front_dist_mm, 0)
        self.assertGreater(latest.min_left_dist_mm, 0)
        self.assertGreater(latest.min_right_dist_mm, 0)
        self.assertGreater(latest.min_back_dist_mm, 0)

    def test_scan_control_methods(self):
        self.assertTrue(self.service.is_scanning)
        self.service.stop_scan()
        self.assertFalse(self.service.is_scanning)

        self.service.start_scan()
        self.assertTrue(self.service.is_scanning)

        self.service.reset_core()
        self.assertTrue(self.service.is_connected)

    def test_available_ports_list(self):
        ports = LidarService.list_available_ports()
        self.assertIn("MOCK_SIMULATOR", ports)


if __name__ == "__main__":
    unittest.main()
