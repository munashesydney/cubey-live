"""
Unit tests for AutoNavigator state machine and trajectory execution.
"""

import time
import unittest
import numpy as np

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
        self.assertGreater(self.navigator._teleop_override_until, time.time())

    def test_mapping_service_integration(self):
        self.mapping_svc.start_mapping()
        self.assertTrue(self.navigator.is_active)

        # Snapshot should contain navigation fields
        snapshot = self.mapping_svc.get_snapshot()
        self.assertIsNotNone(snapshot.planned_path)
        self.assertIsInstance(snapshot.planned_path, list)

        self.mapping_svc.pause_mapping()
        self.assertFalse(self.navigator.is_active)


if __name__ == "__main__":
    unittest.main()
