import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from ros2.nodes.cubey_odometry_node import CubeyOdometryNode
from ros2.nodes import cubey_frontier_explorer_node as frontier_explorer_module
from ros2.nodes.cubey_frontier_explorer_node import CubeyFrontierExplorerNode
from ros2.nodes.cmd_vel_serial_bridge import (
    MinimumEffectiveCommandPulseFilter,
    apply_minimum_effective_command,
)
from src.services.navigation.cubey_nav_service import CubeyNavService


class Nav2IntegrationTests(unittest.TestCase):
    def test_nav2_uses_forward_path_follower_at_drivable_speed(self):
        params = Path("ros2/config/nav2_params.yaml").read_text(encoding="utf-8")

        self.assertIn(
            'plugin: "nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController"',
            params,
        )
        self.assertIn("desired_linear_vel: 0.12", params)

    def test_slam_processes_scans_at_pi_safe_rate(self):
        params = Path("ros2/config/slam_toolbox_params.yaml").read_text(encoding="utf-8")

        self.assertIn("throttle_scans: 2", params)
        self.assertIn("minimum_time_interval: 0.18", params)

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
        command_filter = MinimumEffectiveCommandPulseFilter(390, pulse_frames=3)

        outputs = [command_filter.apply(78, -39, 0) for _ in range(32)]

        self.assertEqual(sum(output[0] for output in outputs), 2340)
        self.assertEqual(sum(output[1] for output in outputs), -1170)
        self.assertEqual(sum(output[2] for output in outputs), 0)
        self.assertEqual(sum(output != (0, 0, 0) for output in outputs), 6)

        pulse_indexes = [
            index for index, output in enumerate(outputs) if output != (0, 0, 0)
        ]
        self.assertEqual(pulse_indexes, [14, 15, 16, 29, 30, 31])

    def test_motor_pulse_filter_resets_pending_pulse_on_direction_reversal(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390, pulse_frames=1)
        for _ in range(3):
            self.assertEqual(command_filter.apply(100, 0, 0), (0, 0, 0))

        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(-100, 0, 0), (-390, 0, 0))

    def test_motor_pulse_filter_cancels_active_burst_on_reversal(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390, pulse_frames=3)
        for _ in range(11):
            self.assertEqual(command_filter.apply(100, 0, 0), (0, 0, 0))
        self.assertEqual(command_filter.apply(100, 0, 0), (390, 0, 0))

        self.assertEqual(command_filter.apply(-100, 0, 0), (0, 0, 0))

    def test_motor_pulse_filter_preserves_stop_and_strong_commands(self):
        command_filter = MinimumEffectiveCommandPulseFilter(390, pulse_frames=3)

        self.assertEqual(command_filter.apply(900, 0, -450), (900, 0, -450))
        self.assertEqual(command_filter.apply(0, 0, 0), (0, 0, 0))

    def test_frontier_success_requires_slam_pose_near_goal(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.robot_pose = (0.0, 0.0, 0.0)

        self.assertFalse(explorer._goal_is_physically_reached((-0.18, -0.15)))
        self.assertTrue(explorer._goal_is_physically_reached((0.05, 0.04)))

    def test_frontier_blacklist_uses_stable_world_coordinate_radius(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.blacklist = []
        explorer.frontier_blacklist_radius_m = 0.40

        with patch("ros2.nodes.cubey_frontier_explorer_node.time.time", return_value=100.0):
            explorer._blacklist_coord((3.25, -0.18))

        self.assertTrue(explorer._coord_is_blacklisted((3.21, 0.01)))
        self.assertTrue(explorer._coord_is_blacklisted((3.48, -0.35)))
        self.assertFalse(explorer._coord_is_blacklisted((3.80, -0.18)))
        self.assertEqual(explorer.blacklist, [(3.25, -0.18, 700.0)])

    def test_frontier_selection_preflights_with_nav2_global_planner(self):
        source = Path("ros2/nodes/cubey_frontier_explorer_node.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("ComputePathToPose", source)
        self.assertIn('ActionClient(self, ComputePathToPose, "compute_path_to_pose")', source)
        self.assertIn("_queue_reachable_frontier_selection(frontiers)", source)

    def test_successful_planner_preflight_dispatches_navigation_goal(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.frontier_selection_generation = 4
        explorer.state = "EXPLORING"
        explorer.active_plan_handle = MagicMock()
        explorer.frontier_plan_queue = [(9.0, 9.0, 0.0, 9.1, 9.1, 3)]
        explorer.planning_frontier = True
        explorer.zero_frontier_cycles = 2
        explorer.get_logger = MagicMock(return_value=MagicMock())
        explorer._send_nav2_goal = MagicMock()
        wrapped_result = MagicMock()
        wrapped_result.status = 4
        wrapped_result.result.path.poses = [MagicMock()]
        future = MagicMock()
        future.result.return_value = wrapped_result
        candidate = (1.0, 2.0, 0.5, 1.1, 2.1, 12)

        with patch.object(
            frontier_explorer_module,
            "GoalStatus",
            MagicMock(STATUS_SUCCEEDED=4),
            create=True,
        ):
            explorer._on_plan_result(future, 4, candidate)

        explorer._send_nav2_goal.assert_called_once_with(
            1.0,
            2.0,
            0.5,
            frontier_coord=(1.1, 2.1),
            purpose=explorer.GOAL_FRONTIER,
        )
        self.assertFalse(explorer.planning_frontier)
        self.assertEqual(explorer.frontier_plan_queue, [])
        self.assertEqual(explorer.zero_frontier_cycles, 0)

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

        dx, dy, dyaw = node._correlate_scans(
            points,
            points,
            0.20,
            -0.15,
            0.05,
            lock_translation=True,
        )

        self.assertEqual(dx, 0.0)
        self.assertEqual(dy, 0.0)
        self.assertEqual(dyaw, 0.0)

    def test_scan_match_accepts_rotation_only_when_lidar_confirms_it(self):
        node = object.__new__(CubeyOdometryNode)
        previous = np.array(
            [[1.0, 0.0], [0.35, 1.15], [-0.9, 0.25], [-0.2, -1.1], [1.4, 0.7]],
            dtype=np.float32,
        )
        angle = 0.08
        cosine = np.cos(angle)
        sine = np.sin(angle)
        current = np.column_stack(
            (
                previous[:, 0] * cosine + previous[:, 1] * sine,
                -previous[:, 0] * sine + previous[:, 1] * cosine,
            )
        )

        dx, dy, dyaw = node._correlate_scans(
            previous,
            current,
            0.0,
            0.0,
            angle,
            lock_translation=True,
        )

        self.assertEqual(dx, 0.0)
        self.assertEqual(dy, 0.0)
        self.assertAlmostEqual(dyaw, angle, places=5)

    def test_stale_lidar_scan_is_ignored_after_odometry_reset(self):
        node = object.__new__(CubeyOdometryNode)
        node.last_scan_time = 42.0
        now = MagicMock()
        now.nanoseconds = int(100.0 * 1e9)
        clock = MagicMock()
        clock.now.return_value = now
        node.get_clock = MagicMock(return_value=clock)
        scan = MagicMock()
        scan.header.stamp.sec = 99
        scan.header.stamp.nanosec = 0

        node._on_laser_scan(scan)

        self.assertEqual(node.last_scan_time, 42.0)

    def test_mapping_completion_distinguishes_dock_failure(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.get_logger = MagicMock(return_value=MagicMock())

        explorer.finalization_at_dock = True
        explorer._complete_mapping()
        self.assertEqual(explorer.state, "COMPLETED")

        explorer.finalization_at_dock = False
        explorer._complete_mapping()
        self.assertEqual(explorer.state, "COMPLETED_AWAY_FROM_DOCK")

    def test_stale_frontier_cancel_cannot_fail_new_return_goal(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.nav_goal_generation = 8
        explorer.state = "RETURNING_TO_DOCK"
        explorer.current_goal_coord = (0.0, 0.0)
        explorer.current_frontier_coord = None
        explorer.active_goal_handle = MagicMock()
        explorer.active_goal_purpose = explorer.GOAL_RETURN
        explorer.get_logger = MagicMock(return_value=MagicMock())
        explorer._initiate_map_finalization = MagicMock()
        stale_result = MagicMock()
        stale_result.result.return_value.status = 5

        explorer._on_goal_result(
            stale_result,
            generation=7,
            purpose=explorer.GOAL_FRONTIER,
        )

        explorer._initiate_map_finalization.assert_not_called()
        self.assertEqual(explorer.state, "RETURNING_TO_DOCK")
        self.assertEqual(explorer.current_goal_coord, (0.0, 0.0))
        self.assertEqual(explorer.active_goal_purpose, explorer.GOAL_RETURN)

    def test_current_return_failure_stops_away_from_dock(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.nav_goal_generation = 8
        explorer.state = "RETURNING_TO_DOCK"
        explorer.current_goal_coord = (0.0, 0.0)
        explorer.current_frontier_coord = None
        explorer.active_goal_handle = MagicMock()
        explorer.active_goal_purpose = explorer.GOAL_RETURN
        explorer.get_logger = MagicMock(return_value=MagicMock())
        explorer._initiate_map_finalization = MagicMock()
        result = MagicMock()
        result.result.return_value.status = 5

        with patch.object(
            frontier_explorer_module,
            "GoalStatus",
            MagicMock(STATUS_SUCCEEDED=4, STATUS_ABORTED=6),
            create=True,
        ):
            explorer._on_goal_result(
                result,
                generation=8,
                purpose=explorer.GOAL_RETURN,
            )

        explorer._initiate_map_finalization.assert_called_once_with()
        self.assertIsNone(explorer.current_goal_coord)
        self.assertIsNone(explorer.active_goal_purpose)

    def test_active_goal_suspends_empty_frontier_completion_checks(self):
        explorer = object.__new__(CubeyFrontierExplorerNode)
        explorer.state = "EXPLORING"
        explorer.latest_map = MagicMock()
        explorer.current_goal_coord = (2.0, 1.0)
        explorer.goal_start_time = time.time()
        explorer.goal_timeout_sec = 35.0
        explorer.planning_frontier = False
        explorer._update_robot_pose_from_tf = MagicMock()
        explorer._extract_frontiers = MagicMock(return_value=[])
        explorer._trigger_auto_stop_sequence = MagicMock()

        explorer._supervision_loop()

        explorer._extract_frontiers.assert_not_called()
        explorer._trigger_auto_stop_sequence.assert_not_called()

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
