import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from ros2.nodes.cubey_odometry_node import CubeyOdometryNode
from ros2.nodes.cubey_frontier_explorer_node import CubeyFrontierExplorerNode
from ros2.nodes.cmd_vel_serial_bridge import (
    MinimumEffectiveCommandPulseFilter,
    apply_minimum_effective_command,
)
from src.services.navigation.cubey_nav_service import CubeyNavService


class Nav2IntegrationTests(unittest.TestCase):
    def test_motor_floor_scales_weak_nav2_vector_without_changing_direction(self):
        self.assertEqual(
            apply_minimum_effective_command(100, -50, 25, 390),
            (390, -195, 98),
        )

    def test_motor_floor_preserves_stop_and_strong_commands(self):
        self.assertEqual(apply_minimum_effective_command(0, 0, 0, 390), (0, 0, 0))
        self.assertEqual(
            apply_minimum_effective_command(250, 0, -800, 390),
            (250, 0, -800),
        )

    def test_motor_pulse_filter_preserves_weak_command_average(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390)

        outputs = [command_filter.apply(78, -39, 0) for _ in range(10)]

        self.assertEqual(sum(output[0] for output in outputs), 780)
        self.assertEqual(sum(output[1] for output in outputs), -390)
        self.assertEqual(sum(output[2] for output in outputs), 0)
        self.assertEqual(sum(output != (0, 0, 0) for output in outputs), 2)

    def test_motor_pulse_filter_resets_pending_pulse_on_direction_reversal(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390)
        for _ in range(3):
            self.assertEqual(command_filter.apply(100, 0, 0), (0, 0, 0))

        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (-390, 0, 0))

    def test_motor_pulse_filter_preserves_stop_and_strong_commands(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390)

        self.assertEqual(command_filter.apply(900, 0, -450), (900, 0, -450))
        self.assertEqual(command_filter.apply(0, 0, 0), (0, 0, 0))

    def test_frontier_success_requires_slam_pose_near_goal(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.robot_pose = (0.0, 0.0, 0.0)

        self.assertFalse(explorer._goal_is_physically_reached((-0.18, -0.15)))
        self.assertTrue(explorer._goal_is_physically_reached((0.05, 0.04)))

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
