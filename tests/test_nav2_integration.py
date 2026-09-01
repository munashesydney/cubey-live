import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from ros2.nodes.cubey_odometry_node import CubeyOdometryNode
from src.services.navigation.cubey_nav_service import CubeyNavService


class Nav2IntegrationTests(unittest.TestCase):
    def test_scan_match_does_not_translate_identical_stationary_scans(self):
        node = object.__new__(CubeyOdometryNode)
        points = np.array(
            [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]],
            dtype=np.float32,
        )

        dx, dy, _ = node._correlate_scans(points, points, 0.04, 0.0, 0.0)

        self.assertEqual(dx, 0.0)
        self.assertEqual(dy, 0.0)

    def test_scan_match_locks_translation_during_rotation(self):
        node = object.__new__(CubeyOdometryNode)
        points = np.array(
            [[1.0, 0.0], [0.2, 1.2], [-0.8, 0.3], [0.1, -1.0]],
            dtype=np.float32,
        )

        dx, dy, _ = node._correlate_scans(
            points,
            points,
            0.20,
            -0.15,
            0.05,
            lock_translation=True,
        )

        self.assertEqual(dx, 0.0)
        self.assertEqual(dy, 0.0)

    def test_stale_heartbeat_is_not_ready(self):
        service = CubeyNavService()
        with patch.object(
            service,
            "_read_ros2_status",
            return_value={"state": "IDLE", "timestamp": time.time() - 30.0},
        ):
            self.assertFalse(service.is_ros2_ready())

    def test_autonomous_mapping_refuses_to_fallback_when_nav2_is_down(self):
        service = CubeyNavService()
        with patch.object(service, "stop_navigation"), patch.object(
            service, "is_ros2_ready", return_value=False
        ):
            self.assertFalse(service.start_exploration())
            self.assertFalse(service._is_exploring)

    @patch("src.services.navigation.cubey_nav_service.threading.Thread")
    @patch("src.services.navigation.cubey_nav_service.get_mapping_service")
    def test_autonomous_mapping_requires_ros_acknowledgement(self, get_mapping_service, thread_class):
        service = CubeyNavService()
        mapping_service = MagicMock()
        get_mapping_service.return_value = mapping_service
        thread_class.return_value = MagicMock()

        with patch.object(service, "stop_navigation"), patch.object(
            service, "is_ros2_ready", return_value=True
        ), patch.object(service, "_send_ros2_command", return_value=True), patch.object(
            service, "_wait_for_ros2_state", return_value=True
        ):
            self.assertTrue(service.start_exploration())

        mapping_service.start_mapping.assert_called_once_with()
        thread_class.return_value.start.assert_called_once_with()
        self.assertTrue(service._is_exploring)
        self.assertEqual(service.telemetry.state, "EXPLORING")

    @patch("src.services.navigation.cubey_nav_service.get_mapping_service")
    def test_missing_ros_acknowledgement_stops_mapping(self, get_mapping_service):
        service = CubeyNavService()
        mapping_service = MagicMock()
        get_mapping_service.return_value = mapping_service

        with patch.object(service, "stop_navigation"), patch.object(
            service, "is_ros2_ready", return_value=True
        ), patch.object(service, "_send_ros2_command", return_value=True), patch.object(
            service, "_wait_for_ros2_state", return_value=False
        ):
            self.assertFalse(service.start_exploration())

        mapping_service.pause_mapping.assert_called_once_with()
        self.assertFalse(service._is_exploring)
        self.assertEqual(service.telemetry.state, "ERROR")

    @patch("src.services.navigation.cubey_nav_service.get_mapping_service")
    def test_reset_mapping_resets_ros_and_legacy_state(self, get_mapping_service):
        service = CubeyNavService()
        mapping_service = MagicMock()
        get_mapping_service.return_value = mapping_service

        with patch.object(service, "stop_navigation"), patch.object(
            service, "is_ros2_ready", return_value=True
        ), patch.object(service, "_send_ros2_command", return_value=True) as send, patch.object(
            service, "_wait_for_ros2_state", return_value=True
        ) as wait:
            self.assertTrue(service.reset_mapping())

        mapping_service.reset_map.assert_called_once_with()
        send.assert_called_once_with("reset")
        wait.assert_called_once()
        self.assertEqual(service.telemetry.state, "IDLE")

    @patch("src.services.navigation.cubey_nav_service.get_mapping_service")
    def test_reset_mapping_fails_closed_when_ros_is_down(self, get_mapping_service):
        service = CubeyNavService()
        with patch.object(service, "stop_navigation"), patch.object(
            service, "is_ros2_ready", return_value=False
        ):
            self.assertFalse(service.reset_mapping())

        get_mapping_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
