"""
Unit tests for AutoNavigator state machine and trajectory execution.
"""

import time
import unittest
import numpy as np
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.lidar_service import LidarPoint, LidarScanData, LidarService
from src.services.mapping_service import MappingService, RobotPose
from src.services.navigation.auto_navigator import AutoNavigator, NavigationState
from src.services.wheels_service import WheelsService


class AutoNavigatorTests(unittest.TestCase):
    """Test AutoNavigator start/stop, teleop overrides, and planning."""

    def setUp(self):
        self.mock_lidar = LidarService(default_port="MOCK_SIMULATOR")
        self.mock_wheels = WheelsService(default_port="MOCK_SIMULATOR")
        self.mapping_svc = MappingService(
            width=50,
            height=50,
            resolution_m=0.05,
            lidar_service=self.mock_lidar,
            wheels_service=self.mock_wheels,
            autonomy_enabled=True,
        )
        self.navigator = self.mapping_svc.navigator

    def tearDown(self):
        self.navigator.stop()
        self.mapping_svc.pause_mapping()
        self.mock_lidar.disconnect()
        self.mock_wheels.disconnect()

    def test_navigator_start_and_stop(self):
        self.assertEqual(self.navigator.state, NavigationState.IDLE)
        self.navigator.start()
        self.assertTrue(self.navigator.is_active)

        self.navigator.stop()
        self.assertEqual(self.navigator.state, NavigationState.IDLE)
        self.assertFalse(self.navigator.is_active)

    def test_yield_to_teleop(self):
        self.navigator.start()
        self.navigator.yield_to_teleop(duration_s=2.0)
        self.assertGreater(self.navigator._teleop_override_until, time.monotonic())

    def test_mapping_service_integration(self):
        self.mapping_svc.start_mapping()
        self.assertTrue(self.navigator.is_active)

        # Snapshot should contain navigation fields
        snapshot = self.mapping_svc.get_snapshot()
        self.assertIsNotNone(snapshot.planned_path)
        self.assertIsInstance(snapshot.planned_path, list)

        self.mapping_svc.pause_mapping()
        self.assertFalse(self.navigator.is_active)

    def test_forward_collision_zone_detects_close_wall(self):
        wall = LidarPoint(angle_deg=0.0, distance_mm=220.0, quality=60)
        self.mock_lidar.latest_scan = LidarScanData(
            points=[wall],
            timestamp=time.time(),
            scan_rate_hz=10.0,
            point_count=1,
        )

        reason = self.navigator._collision_reason("forward")

        self.assertIsNotNone(reason)
        self.assertIn("220mm", reason)

    def test_chassis_reflection_does_not_block_any_motion(self):
        # Exact signature observed on Cubey: 56-58 mm around zero degrees.
        reflection = LidarPoint(angle_deg=351.4, distance_mm=56.0, quality=60)
        self.mock_lidar.latest_scan = LidarScanData(points=[reflection])

        commands = [
            "forward",
            "backward",
            "strafeLeft",
            "strafeRight",
            "forwardLeft",
            "forwardRight",
            "backwardLeft",
            "backwardRight",
            "rotateLeft",
            "rotateRight",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.navigator._collision_reason(command))

    def test_mapping_joystick_gate_allows_motion_with_observed_self_return(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.mock_lidar.connect(port="MOCK_SIMULATOR")
        points = [
            LidarPoint(angle_deg=float(i * 10), distance_mm=1500.0, quality=60)
            for i in range(36)
        ]
        points.append(LidarPoint(angle_deg=351.4, distance_mm=56.0, quality=60))
        self.mock_lidar.latest_scan = LidarScanData(
            points=points,
            timestamp=time.time(),
            scan_rate_hz=10.0,
            point_count=len(points),
        )

        for command in [
            "forward",
            "strafeLeft",
            "strafeRight",
            "rotateLeft",
            "rotateRight",
        ]:
            with self.subTest(command=command):
                safe, reason = self.navigator.authorize_manual_motion(command)
                self.assertTrue(safe, reason)

    def test_directional_obstacle_only_blocks_motion_toward_it(self):
        front_wall = LidarPoint(angle_deg=0.0, distance_mm=240.0, quality=60)
        self.mock_lidar.latest_scan = LidarScanData(points=[front_wall])

        self.assertIsNotNone(self.navigator._collision_reason("forward"))
        self.assertIsNone(self.navigator._collision_reason("backward"))

    def test_rotation_checks_entire_footprint(self):
        side_wall = LidarPoint(angle_deg=90.0, distance_mm=220.0, quality=60)
        self.mock_lidar.latest_scan = LidarScanData(points=[side_wall])

        self.assertIsNotNone(self.navigator._collision_reason("rotateRight"))

    def test_no_route_has_bounded_recovery_and_faults(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.navigator._running = True
        self.navigator._stop_event.clear()
        self.navigator._recovery_attempts = self.navigator.max_recovery_attempts
        self.navigator.frontier_detector.find_frontiers = MagicMock(
            return_value=[SimpleNamespace(centroid_world=(0.8, 0.8))]
        )
        self.navigator.path_planner.plan_path = MagicMock(return_value=None)

        self.navigator._plan_next_frontier()

        self.assertEqual(self.navigator.state, NavigationState.FAULT)
        self.assertIn("no_reachable_frontier", self.navigator.fault_reason)

    def test_planning_rejection_does_not_blacklist_frontier_before_rescan(self):
        target = (0.8, 0.8)
        self.navigator._running = True
        self.navigator.frontier_detector.find_frontiers = MagicMock(
            return_value=[SimpleNamespace(centroid_world=target)]
        )
        self.navigator.path_planner.plan_path = MagicMock(return_value=None)
        self.navigator.path_planner.last_failure_reason = (
            "no_path_through_known_free_space"
        )
        self.navigator._bounded_rotation = MagicMock()

        self.navigator._plan_next_frontier()

        self.assertFalse(self.navigator._target_is_blocked(target))
        self.assertEqual(
            self.navigator.last_planning_rejection,
            "no_path_through_known_free_space",
        )

    def test_stale_sensor_during_motion_latches_emergency_stop(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.mock_lidar._is_connected = True
        self.mock_lidar._is_scanning = False
        self.navigator._running = True
        self.navigator._stop_event.clear()
        self.navigator.state = NavigationState.NAVIGATING
        self.navigator._last_command = "forward"

        thread = __import__("threading").Thread(target=self.navigator._navigation_loop)
        thread.start()
        thread.join(timeout=0.5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(self.navigator.state, NavigationState.E_STOPPED)
        self.assertTrue(self.mock_wheels.is_emergency_stopped)

    def test_progress_watchdog_stops_frozen_pose(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.navigator._running = True
        self.navigator.state = NavigationState.NAVIGATING
        self.navigator.current_path = [(0.0, 0.0), (1.0, 0.0)]
        self.navigator.target_frontier = (1.0, 0.0)
        self.navigator._last_progress_pose = (0.0, 0.0, 0.0)
        self.navigator._last_progress_time = (
            time.monotonic() - self.navigator.progress_timeout_s - 0.1
        )

        self.navigator._follow_path()

        self.assertEqual(self.navigator.state, NavigationState.RECOVERY)
        self.assertEqual(self.navigator._last_command, "stop")
        self.assertEqual(self.navigator.current_path, [])

    def test_manual_motion_is_rejected_when_lidar_is_unhealthy(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.mock_lidar._is_connected = True
        self.mock_lidar._is_scanning = False

        safe, reason = self.navigator.authorize_manual_motion("forward")

        self.assertFalse(safe)
        self.assertIn("lidar", reason)
        self.assertFalse(self.mock_wheels.is_emergency_stopped)

    def test_manual_motion_does_not_require_autonomous_telemetry_heartbeat(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.mock_lidar.connect(port="MOCK_SIMULATOR")
        self.mock_lidar.latest_scan = LidarScanData(
            points=[
                LidarPoint(angle_deg=float(i * 10), distance_mm=1500.0, quality=60)
                for i in range(36)
            ],
            timestamp=time.time(),
            scan_rate_hz=10.0,
            point_count=36,
        )
        self.mock_wheels.telemetry.timestamp = time.time() - 90.0

        safe, reason = self.navigator.authorize_manual_motion("forward")

        self.assertTrue(safe, reason)

    def test_estop_can_be_rearmed_when_wheel_telemetry_is_stale(self):
        self.mock_wheels.connect(port="MOCK_SIMULATOR")
        self.mock_wheels.emergency_stop("test")
        self.mock_wheels.telemetry.timestamp = time.time() - 90.0

        cleared, reason = self.navigator.clear_emergency_stop()

        self.assertTrue(cleared, reason)
        self.assertFalse(self.mock_wheels.is_emergency_stopped)


if __name__ == "__main__":
    unittest.main()
