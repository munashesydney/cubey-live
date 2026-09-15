"""Behavioral checks for measured heading, transport faults, and mapping missions."""
import json
import math
import time
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml

from ros2.nodes.imu_support import (ImuPacketClock, HeadingHistory, conjugate,
    quaternion_multiply, quaternion_from_euler, match_translation, yaw, wrap, heading_jump_metrics)
from ros2.nodes import cubey_odometry_node as odometry
from ros2.nodes import rplidar_c1_node as rplidar
from ros2.nodes import cubey_frontier_explorer_node as explorer_module
from ros2.nodes.cubey_frontier_explorer_node import CubeyFrontierExplorerNode
from ros2.nodes.cmd_vel_serial_bridge import CmdVelSerialBridgeNode, MinimumEffectiveCommandPulseFilter
from src.services.navigation.live_pose import read_live_pose
from src.services.navigation.cubey_nav_service import CubeyNavService


def packet(seq=1, sensor_us=1_000_000, **kwargs):
    fields = dict(ok=1, boot=123, epoch=0, seq=seq, t_us=sensor_us, age_ms=0,
                  cal=2, qw=1, qx=0, qy=0, qz=0)
    fields.update(kwargs)
    return "IMU:"+",".join(f"{key}={value}" for key, value in fields.items())


def test_packet_clock_rejects_duplicates_old_sequences_and_backlog():
    clock = ImuPacketClock()
    assert clock.parse(packet(), 100).stamp == 100
    assert clock.parse(packet(), 100.1) is None
    assert clock.parse(packet(0, 900_000), 100.1) is None
    assert clock.parse(packet(2, 1_020_000), 100.3) is None
    assert clock.reason == "IMU serial backlog"
    assert clock.parse(packet(3, 1_400_000), 100.4).stamp == pytest.approx(100.4)


@pytest.mark.parametrize("changes", [dict(qw="nan"), dict(qw=0), dict(ok=0), dict(age_ms=250), dict(t_us=-1)])
def test_invalid_imu_packets_are_not_published(changes):
    assert ImuPacketClock().parse(packet(**changes), 100) is None


def test_old_diagnostic_without_timestamps_is_not_a_navigation_sample():
    assert ImuPacketClock().parse("IMU:ok=1,qw=1,qx=0,qy=0,qz=0", 100) is None


def test_sensor_epoch_and_boot_changes_are_explicit_and_clock_can_restart():
    clock = ImuPacketClock()
    clock.parse(packet(), 100)
    sample = clock.parse(packet(2, 20_000, epoch=1), 100.2)
    assert sample.stream == (123, 1)
    sample = clock.parse(packet(1, 10_000, boot=456), 101)
    assert sample.stream == (456, 0)


def test_sequence_wrap_and_clock_regression():
    clock = ImuPacketClock()
    clock.parse(packet(0xffffffff), 100)
    assert clock.parse(packet(0, 1_020_000), 100.02) is not None
    assert clock.parse(packet(1, 100), 100.04) is None
    assert "regressed" in clock.reason


@pytest.mark.parametrize("jump", [800.0, -800.0])
def test_host_clock_step_preserves_fresh_samples_and_marks_new_clock_epoch(jump):
    clock = ImuPacketClock()
    clock.parse(packet(), 100, 10)
    sample = clock.parse(packet(2, 1_020_000), 100.02+jump, 10.02)
    assert sample.stamp == pytest.approx(100.02+jump)
    assert clock.host_clock_generation == 1
    # A real serial delay following the clock change is still rejected.
    assert clock.parse(packet(3, 1_040_000), 100.4+jump, 10.4) is None
    assert clock.reason == "IMU serial backlog"


def test_serial_backlog_does_not_masquerade_as_a_host_clock_step():
    clock = ImuPacketClock()
    clock.parse(packet(), 100, 10)
    assert clock.parse(packet(2, 1_020_000), 100.4, 10.4) is None
    assert clock.host_clock_generation == 0


def test_reset_does_not_accept_an_unrelated_idle_heartbeat():
    service = CubeyNavService()
    now = time.time()
    with patch.object(service, "_read_ros2_status", return_value={"state": "IDLE", "timestamp": now+1, "mission_id": "old"}):
        assert not service._wait_for_ros2_state({"IDLE"}, now, timeout_s=0.01, mission_id="new")


def test_failed_reset_keeps_web_map_and_surfaces_sensor_failure():
    service = CubeyNavService()
    with patch.object(service, "stop_navigation"), patch.object(service, "is_ros2_ready", return_value=True), \
         patch("src.services.navigation.cubey_nav_service.uuid.uuid4", return_value="reset-test"), \
         patch.object(service, "_send_ros2_command", return_value=True), \
         patch.object(service, "_wait_for_ros2_state", return_value=False), \
         patch.object(service, "_read_ros2_status", return_value={"mission_id": "reset-test", "failure_reason": "Waiting for IMU"}), \
         patch("src.services.navigation.cubey_nav_service.get_mapping_service") as mapping:
        assert not service.reset_mapping()
        assert service.last_reset_error == "Waiting for IMU"
        mapping.assert_not_called()


def test_heading_interpolation_uses_short_path_through_pi():
    history = HeadingHistory()
    history.add(1, math.radians(179))
    history.add(1.02, math.radians(-179))
    assert abs(history.at(1.01)) == pytest.approx(math.pi)
    assert history.at(2) is None
    assert history.add(1.01, 0) is False


def test_heading_batch_interpolation_matches_individual_beam_times():
    history = HeadingHistory()
    history.add(1.00, 0.0)
    history.add(1.02, 0.2)
    history.add(1.04, 0.4)

    assert history.at_many([1.00, 1.01, 1.03, 1.04]) == pytest.approx([0.0, 0.1, 0.3, 0.4])


def test_recorded_073044_heading_sequence_is_not_a_jump():
    samples = [(0.8018434, -1.6342370522346763), (.8210404, -1.5991323628367224),
               (.8422453, -1.5606895468862603), (.8683724, -1.5244113573763922),
               (.8828423, -1.4872128802112097), (.9012413, -1.45026103489693),
               (.9210503, -1.4118857426803344), (.9420505, -1.3744748752450286),
               (.9691193, -1.3349005855618448), (.9927075, -1.2948358739170016)]
    result = heading_jump_metrics(samples, 1.0012703, math.radians(-71.82350074730024))
    assert result["pair_rate_rad_s"] > 4.
    assert 1.5 < result["window_rate_rad_s"] < 2.5
    assert not result["jump"]


def test_sustained_excessive_turn_rate_is_not_hidden_by_pair_allowance():
    samples = [(0., 0.), (.02, .09), (.04, .18), (.06, .27)]
    assert heading_jump_metrics(samples, .08, .36)["jump"]
    assert heading_jump_metrics([(0., 0.)], .02, .2)["jump"]
    assert not heading_jump_metrics([(0., math.radians(179))], .02, math.radians(-179))["jump"]


@pytest.mark.parametrize("mount_angles", [(0, 0, 0), (0, 0, math.pi), (0.2, -0.1, math.pi/2), (math.pi/2, 0, 0)])
def test_full_mounting_rotation_preserves_left_turn_direction(mount_angles):
    mount = quaternion_from_euler(*mount_angles)
    sensor = quaternion_multiply(quaternion_from_euler(0, 0, math.pi/2), mount)
    body = quaternion_multiply(sensor, conjugate(mount))
    assert yaw(body) == pytest.approx(math.pi/2)
    assert yaw(tuple(-v for v in body)) == pytest.approx(math.pi/2)


def room_points():
    a = np.linspace(-2, 2, 65)
    return np.concatenate((np.column_stack((a, np.full_like(a, -2))),
                           np.column_stack((np.full_like(a, 2), a)),
                           np.column_stack((a[::-1], np.full_like(a, 2))),
                           np.column_stack((np.full_like(a, -2), a[::-1]))))


@pytest.mark.parametrize("translation,rotation", [((0, 0), 0), ((0.05, -0.03), 0), ((0, 0), 0.4), ((0.02, 0.04), -0.25)])
def test_scan_translation_uses_measured_rotation_and_base_sensor_offset(translation, rotation):
    previous = room_points()
    c, s = math.cos(rotation), math.sin(rotation)
    offset = np.array([-0.035, 0])
    # Generate a physical scan from an offset sensor, then express in base frame.
    current_sensor = (previous-np.array(translation)) @ np.array([[c, -s], [s, c]])-offset
    current_base = current_sensor+offset
    result = match_translation(previous, current_base, rotation)
    assert result is not None
    assert result[:2] == pytest.approx(translation, abs=0.002)


def test_unobservable_wall_and_impossible_displacement_are_rejected():
    x = np.linspace(-2, 2, 100)
    wall = np.column_stack((x, np.ones_like(x)))
    assert match_translation(wall, wall, 0) is None
    previous = room_points()
    assert match_translation(previous, previous-0.4, 0, max_translation=0.1) is None


def imu_message(stamp, heading, mount=(0, 0, 0, 1)):
    q = quaternion_multiply(quaternion_from_euler(0, 0, heading), mount)
    return NS(header=NS(stamp=NS(sec=int(stamp), nanosec=int((stamp-int(stamp))*1e9)), frame_id="imu_link"),
              orientation=NS(x=q[0], y=q[1], z=q[2], w=q[3]), orientation_covariance=[0.01]*9,
              angular_velocity_covariance=[0.]*9, linear_acceleration_covariance=[0.]*9)


def measurement_node(now=100):
    node = object.__new__(odometry.CubeyOdometryNode)
    node._now = MagicMock(return_value=now)
    node.mount = (0, 0, 0, 1)
    node.fault = ""
    node.imu_stream = [123, 0]
    node.imu_healthy = True
    node.imu_status_time = now
    node.pub_imu = MagicMock()
    node.pub_translation = MagicMock()
    node.pub_slam_scan = MagicMock()
    node.get_logger = MagicMock(return_value=MagicMock())
    node._reset_state()
    return node


def test_heading_updates_with_no_motor_command_and_reset_changes_reference():
    node = measurement_node()
    with patch.object(odometry, "Imu", side_effect=lambda: imu_message(0, 0), create=True):
        node._now.return_value = 100.02
        node._on_imu(imu_message(100.02, 1.0))
        node._now.return_value = 100.12
        node._on_imu(imu_message(100.12, 1.2))
        assert yaw(tuple(getattr(node.pub_imu.publish.call_args.args[0].orientation, axis) for axis in ("x", "y", "z", "w"))) == pytest.approx(0.2)
        node._handle_reset_odometry(None, NS())
        assert not node.history.samples
        node._now.return_value = 100.14
        node._on_imu(imu_message(100.14, 1.2))
        assert node.history.samples[-1][1] == pytest.approx(0)


@pytest.mark.parametrize("fault", ["latched", "imu_stale", "translation_stale", "filter_stale", "filter_rotating", "filter_drifting", "imu_unhealthy"])
def test_slam_scan_gate_blocks_invalid_localization_even_if_filter_keeps_publishing(fault):
    node = measurement_node()
    node._now.return_value = 100.1
    node.last_imu_time = node.last_translation_time = node.filtered_stamp = 100.1
    node.filtered_pose = (0., 0., 0.)
    if fault == "latched": node.fault = "IMU heading jump"
    if fault == "imu_stale": node.last_imu_time = 99.
    if fault == "translation_stale": node.last_translation_time = 99.
    if fault == "filter_stale": node.filtered_stamp = 99.
    if fault == "filter_rotating": node.filtered_pose = (0., 0., 1.)
    if fault == "filter_drifting": node.filtered_pose = (1., 0., 0.)
    if fault == "imu_unhealthy": node.imu_healthy = False
    node._forward_slam_scan(NS(), 0.)
    node.pub_slam_scan.publish.assert_not_called()


def test_slam_scan_gate_passes_measured_scans_and_closes_on_reset():
    node = measurement_node()
    node._now.return_value = 100.1
    node.last_imu_time = node.last_translation_time = node.filtered_stamp = 100.1
    node.filtered_pose = (0., 0., 0.)
    scan = NS()
    node._forward_slam_scan(scan, 0.)
    node.pub_slam_scan.publish.assert_called_once_with(scan)
    node._handle_reset_odometry(None, NS())
    node._forward_slam_scan(scan, 0.)
    assert node.pub_slam_scan.publish.call_count == 1


def test_slam_scan_gate_keeps_slam_alive_during_a_rapid_turn():
    node = measurement_node()
    node._now.return_value = 100.1
    node.imu_status_time = node.last_imu_time = node.last_translation_time = node.filtered_stamp = 100.1
    node.filtered_pose = (0., 0., 0.)
    node.wz = 0.36
    scan = NS()

    node._forward_slam_scan(scan, 0.)
    node.pub_slam_scan.publish.assert_called_once_with(scan)
    assert node.slam_scan_gate_reason == ""


def lidar_node(min_deskew_coverage=0.98):
    node = object.__new__(rplidar.RPLidarC1Node)
    node.headings = HeadingHistory()
    node.min_deskew_coverage = min_deskew_coverage
    node.min_range = 0.05
    node.max_range = 12.0
    node.frame_id = "laser"
    node.deskew_dropped_scans = 0
    node.last_deskew_drop_reason = ""
    node._last_deskew_warning_at = float("-inf")
    node.pub_scan = MagicMock()
    node.get_logger = MagicMock(return_value=MagicMock())
    return node


def test_lidar_refuses_to_publish_a_partially_deskewed_scan():
    node = lidar_node()
    node.headings.add(100.0, 0.0)
    node.headings.add(100.1, 0.1)
    # The first beam predates the available IMU history. It must not quietly
    # remain raw while later beams are deskewed.
    points = [(0.0, 1.0, 20, 99.8), (0.1, 1.0, 20, 100.1)]

    node._publish_laser_scan(points, 0.1)

    node.pub_scan.publish.assert_not_called()
    assert node.deskew_dropped_scans == 1
    assert node.last_deskew_drop_reason == "insufficient_heading_coverage"


def test_lidar_publishes_only_fully_deskewed_common_time_scans():
    node = lidar_node()
    node.headings.add(100.0, 0.0)
    node.headings.add(100.1, 0.1)
    message = NS(header=NS())
    fake_time = MagicMock(return_value=NS(to_msg=lambda: NS(sec=100, nanosec=100_100_000)))
    with patch.object(rplidar, "LaserScan", return_value=message, create=True), \
         patch.object(rplidar, "Time", fake_time, create=True):
        node._publish_laser_scan([(0.0, 1.0, 20, 100.0), (0.1, 1.0, 20, 100.1)], 0.1)

    node.pub_scan.publish.assert_called_once_with(message)
    assert message.time_increment == 0.0
    assert node.deskew_dropped_scans == 0


def test_web_scan_projection_uses_the_scan_timestamp_transform():
    node = object.__new__(CubeyFrontierExplorerNode)
    node.tf_buffer = MagicMock()
    node.tf_buffer.lookup_transform.return_value = NS(
        transform=NS(
            translation=NS(x=1.2, y=-0.4),
            rotation=NS(x=0.0, y=0.0, z=math.sin(0.25), w=math.cos(0.25)),
        )
    )
    scan = NS(header=NS(stamp=NS(sec=100, nanosec=123)))

    fake_time = MagicMock()
    fake_time.from_msg.return_value = "scan-time"
    with patch.object(explorer_module, "Time", fake_time, create=True):
        pose = node._scan_pose_from_tf(scan)

    fake_time.from_msg.assert_called_once_with(scan.header.stamp)
    node.tf_buffer.lookup_transform.assert_called_once_with("map", "base_link", "scan-time")
    assert pose == pytest.approx((1.2, -0.4, 0.5))


def test_slam_uses_gated_scan_topic():
    from pathlib import Path
    config = yaml.safe_load(Path("ros2/config/slam_toolbox_params.yaml").read_text())
    assert config["slam_toolbox"]["ros__parameters"]["scan_topic"] == "/scan/slam"


def test_slam_cannot_apply_catastrophic_global_loop_closures():
    from pathlib import Path
    config = yaml.safe_load(Path("ros2/config/slam_toolbox_params.yaml").read_text())
    params = config["slam_toolbox"]["ros__parameters"]
    assert params["use_scan_matching"] is True
    assert params["do_loop_closing"] is False


def test_translation_publication_preserves_forwarded_laser_frame():
    node = measurement_node()
    node.laser_x = node.laser_y = node.laser_yaw = 0.
    node._now.return_value = 100.1
    node.history.add(100.1, 0.)
    node.last_scan_time = 100.0
    node.prev_points = np.ones((40, 2))
    node.last_imu_time = node.filtered_stamp = 100.1
    node.filtered_pose = (0., 0., 0.)
    scan = NS(header=NS(stamp=NS(sec=100, nanosec=100_000_000), frame_id="laser"),
              ranges=[1.]*40, angle_min=0., angle_increment=.1, range_min=.1, range_max=10.)
    odom = NS(pose=NS(pose=NS(position=NS(), orientation=NS()), covariance=[0.]*36),
              twist=NS(twist=NS(linear=NS()), covariance=[0.]*36))
    with patch.object(odometry, "Odometry", return_value=odom, create=True), \
         patch.object(odometry, "match_translation", return_value=(0., 0., .001)):
        node._on_laser_scan(scan)
    assert node.pub_translation.publish.call_args.args[0].header.frame_id == "odom"
    assert node.pub_slam_scan.publish.call_args.args[0].header.frame_id == "laser"
    assert scan.header.frame_id == "laser"


def test_imu_restart_latches_fault_until_explicit_session_reset():
    node = measurement_node()
    node._on_imu_status(NS(data=json.dumps({"stream": [123, 1], "healthy": True})))
    assert "restarted" in node.fault
    node._now.return_value = 100.1
    node._on_imu(imu_message(100.1, 0))
    node.pub_imu.publish.assert_not_called()
    response = node._handle_reset_odometry(None, NS())
    assert response.success
    assert not node.fault


def test_heading_jump_logs_measurements_once_and_keeps_existing_stop_threshold():
    node = measurement_node()
    with patch.object(odometry, "Imu", side_effect=lambda: imu_message(0, 0), create=True):
        node._now.return_value = 100.02
        node._on_imu(imu_message(100.02, 1.0))
        node._now.return_value = 100.05
        node._on_imu(imu_message(100.04, 1.2))
        node._on_imu(imu_message(100.06, 1.3))
    node.get_logger().error.assert_called_once()
    message = node.get_logger().error.call_args.args[0]
    assert message.startswith("IMU_HEADING_JUMP ")
    diagnostic = json.loads(message.split(" ", 1)[1])
    assert diagnostic["interval_s"] == pytest.approx(.02)
    assert diagnostic["rate_rad_s"] == pytest.approx(10.)
    assert diagnostic["sample_age_s"] == pytest.approx(.01)
    assert diagnostic["limit_rad_s"] == 4.
    assert len(diagnostic["recent_accepted_headings"]) == 1
    assert diagnostic["previous_quaternion_xyzw"] is not None
    assert node.fault
    assert node.pub_imu.publish.call_count == 1


def test_bridge_blocks_stale_supervisor_and_old_autonomous_commands():
    node = object.__new__(CmdVelSerialBridgeNode)
    node.motion_ready = True
    node.motion_status_time = time.monotonic()
    node.motion_state = "RETURNING_TO_DOCK"
    assert node._motion_allowed("ros")
    assert not node._motion_allowed("teleop")
    node.motion_state = "IDLE"
    assert not node._motion_allowed("ros")
    assert node._motion_allowed("teleop")
    node.motion_status_time -= 1
    assert not node._motion_allowed("teleop")


def mission_node():
    node = object.__new__(CubeyFrontierExplorerNode)
    node.navigation_health = MagicMock()
    node.navigation_health.ready.return_value = True
    node.navigation_health.reason.return_value = ""
    node._sensors_ready = MagicMock(return_value=True)
    node.latest_map = NS()
    node.last_map_time = time.time()
    node.mission_generation = 7
    node.state = "RETURNING_TO_DOCK"
    node.start_pose = (1., 2., 0.5)
    node.robot_pose = node.start_pose
    node.home_captured = True
    node.pre_return_map_saved = False
    node.pre_return_graph_saved = False
    node.map_export_locked = False
    node.slam_measurements_paused = False
    node._pose_fresh = MagicMock(return_value=True)
    node._hold_motion = MagicMock()
    node._cancel_active_nav_goal = MagicMock()
    node.get_logger = MagicMock(return_value=MagicMock())
    return node


def test_home_success_checks_heading_and_freshness_not_just_proximity():
    node = mission_node()
    assert node._dock_is_physically_reached()
    node.robot_pose = (1, 2, 1)
    assert not node._dock_is_physically_reached()
    node.robot_pose = (1.2, 2, 0.5)
    assert not node._dock_is_physically_reached()
    node.robot_pose = node.start_pose
    node._pose_fresh.return_value = False
    assert not node._dock_is_physically_reached()


def test_stop_invalidates_a_pending_reset_callback():
    node = mission_node()
    node._stop_exploration()
    node.reset_filter_client = MagicMock()
    node._on_measurements_reset(MagicMock(), 7)
    node.reset_filter_client.call_async.assert_not_called()
    assert node.state == "IDLE"


def test_failed_measurement_reset_never_resets_filter_or_starts_slam():
    node = mission_node()
    node.state = "RESETTING"
    node.reset_filter_client = MagicMock()
    response = MagicMock()
    response.result.return_value.success = False
    node._on_measurements_reset(response, 7)
    assert node.state == "ERROR"
    node.reset_filter_client.call_async.assert_not_called()
    node._hold_motion.assert_called()


def test_saved_occupancy_map_serializes_graph_without_starting_return():
    node = mission_node()
    node.pre_return_map_base = "checkpoint"
    node._queue_reachable_dock_selection = MagicMock()
    node.serialize_client = MagicMock()
    graph_future = MagicMock()
    node.serialize_client.call_async.return_value = graph_future
    result = MagicMock()
    result.result.return_value.result = True
    with patch.object(
        explorer_module,
        "SerializePoseGraph",
        NS(Request=lambda: NS(filename=None), Response=NS(RESULT_SUCCESS=0)),
        create=True,
    ):
        node._on_pre_return_map_saved(result, 7)
    node._queue_reachable_dock_selection.assert_not_called()
    assert node.pre_return_map_saved
    node.serialize_client.call_async.assert_called_once()
    assert node.serialize_client.call_async.call_args.args[0].filename == "checkpoint"
    node._hold_motion.assert_not_called()


def checkpoint_wait_node():
    node = mission_node()
    node.pre_return_map_saved = False
    node.pre_return_graph_saved = False
    node.return_localization_pending = True
    node.return_ready_since = None
    node.operation_deadline = time.time()+20
    node.last_map_time = time.time()
    node._update_robot_pose_from_tf = MagicMock()
    node._sensors_ready = MagicMock(return_value=True)
    node._pose_fresh.return_value = False
    node._export_live_pose = MagicMock()
    node._queue_reachable_dock_selection = MagicMock()
    node._fail_mission = MagicMock()
    return node


def localization_wait_node():
    node = mission_node()
    node.localization_interruptions = []
    node.last_valid_pose = node.robot_pose
    node.current_goal_coord = (3., 4.)
    node.current_goal_yaw = 0.7
    node.current_frontier_coord = None
    node.active_goal_purpose = node.GOAL_RETURN
    node.odom_health = {"reset_time": 100., "fault": ""}
    node.nav_client = MagicMock()
    node.planner_client = MagicMock()
    node._send_nav2_goal = MagicMock()
    node._pause_for_localization()
    return node


def test_localization_delay_stops_and_replans_original_destination_after_stability():
    node = localization_wait_node()
    assert node.state == "RECOVERING_LOCALIZATION"
    node._cancel_active_nav_goal.assert_called_once()
    node._hold_motion.assert_called()
    node._recover_localization_tick(False)
    node._send_nav2_goal.assert_not_called()
    node._recover_localization_tick(True)
    node._send_nav2_goal.assert_not_called()
    node.localization_ready_since = time.monotonic()-2
    node._recover_localization_tick(False)
    assert node.localization_ready_since is None
    node._recover_localization_tick(True)
    node.localization_ready_since = time.monotonic()-2
    node._recover_localization_tick(True)
    assert node.state == "RETURNING_TO_DOCK"
    node._send_nav2_goal.assert_called_once_with(3., 4., 0.7, frontier_coord=None, purpose=node.GOAL_RETURN)


@pytest.mark.parametrize("failure", ["sensor_reset", "reference", "position", "heading", "timeout"])
def test_localization_recovery_never_resumes_after_reference_fault_or_timeout(failure):
    node = localization_wait_node()
    if failure == "sensor_reset": node.odom_health["fault"] = "IMU restarted"
    if failure == "reference": node.odom_health["reset_time"] = 101.
    if failure == "position": node.robot_pose = (2., 2., .5)
    if failure == "heading": node.robot_pose = (1., 2., 1.5)
    if failure == "timeout": node.localization_deadline = time.monotonic()-1
    node.localization_ready_since = time.monotonic()-2
    node._recover_localization_tick(True)
    assert node.state == "ERROR"
    node._send_nav2_goal.assert_not_called()


def test_repeated_localization_interruptions_fail_closed():
    node = localization_wait_node()
    node.state = "RETURNING_TO_DOCK"
    node.localization_interruptions = [time.monotonic()]*3
    node._pause_for_localization()
    assert node.state == "ERROR"


def test_stop_during_localization_wait_prevents_automatic_resume():
    node = localization_wait_node()
    node._stop_exploration()
    assert node.state == "IDLE"
    node._send_nav2_goal.assert_not_called()


def test_rejected_navigation_goal_pauses_without_blacklisting_or_spending_attempt():
    node = localization_wait_node()
    node.state = "RETURNING_TO_DOCK"
    node.nav_goal_generation = 5
    node.return_attempts = 1
    node._handle_goal_failure = MagicMock()
    result = MagicMock()
    result.result.return_value.accepted = False
    node._on_goal_response(result, 5, node.GOAL_RETURN)
    assert node.state == "RECOVERING_NAVIGATION"
    assert node.return_attempts == 0
    node._handle_goal_failure.assert_not_called()


def test_inactive_navigation_cannot_exhaust_frontiers():
    node = mission_node()
    node.state = "EXPLORING"
    node.navigation_health.ready.return_value = False
    node._update_robot_pose_from_tf = MagicMock()
    node._pause_for_navigation = MagicMock()
    node._extract_frontiers = MagicMock()
    node._trigger_auto_stop_sequence = MagicMock()
    node._supervision_loop()
    node._pause_for_navigation.assert_called_once()
    node._extract_frontiers.assert_not_called()
    node._trigger_auto_stop_sequence.assert_not_called()


def test_checkpoint_save_tolerates_stale_localization_only_while_stopped():
    node = checkpoint_wait_node()
    node._safety_tick()
    node._hold_motion.assert_called_once()
    node._fail_mission.assert_not_called()
    node._queue_reachable_dock_selection.assert_not_called()


def test_checkpoint_recovery_requires_continuously_fresh_pose_before_return():
    node = checkpoint_wait_node()
    node.pre_return_map_saved = True
    node.pre_return_graph_saved = True
    node._safety_tick()
    node._queue_reachable_dock_selection.assert_not_called()
    node._pose_fresh.return_value = True
    node._safety_tick()
    node._queue_reachable_dock_selection.assert_not_called()
    node.return_ready_since = time.monotonic()-1
    node._pose_fresh.return_value = False
    node._safety_tick()
    assert node.return_ready_since is None
    node._pose_fresh.return_value = True
    node._safety_tick()
    node.return_ready_since = time.monotonic()-1
    node._safety_tick()
    node._queue_reachable_dock_selection.assert_called_once()
    assert not node.return_localization_pending


@pytest.mark.parametrize(
    "map_saved,graph_saved,should_return",
    [(False, False, False), (True, False, False), (True, True, True)],
)
def test_return_waits_until_complete_snapshot_is_durable(
    map_saved, graph_saved, should_return
):
    node = checkpoint_wait_node()
    node.pre_return_map_saved = map_saved
    node.pre_return_graph_saved = graph_saved
    node._pose_fresh.return_value = True
    node._safety_tick()
    node.return_ready_since = time.monotonic()-1
    node._safety_tick()
    assert node._queue_reachable_dock_selection.call_count == int(should_return)
    node._fail_mission.assert_not_called()
    assert node.state == "RETURNING_TO_DOCK"


def test_map_save_request_uses_persistent_nav2_service_fields():
    with patch.object(explorer_module, "SaveMap", NS(Request=lambda: NS()), create=True):
        req = CubeyFrontierExplorerNode._map_save_request("/maps/test")
    assert req.map_topic == "/map"
    assert req.map_url == "/maps/test"
    assert (req.image_format, req.map_mode, req.free_thresh, req.occupied_thresh) == ("pgm", "trinary", .25, .65)


@pytest.mark.parametrize("map_saved,graph_saved", [(False, False), (True, False), (True, True)])
def test_checkpoint_save_and_localization_wait_are_bounded(map_saved, graph_saved):
    node = checkpoint_wait_node()
    node.pre_return_map_saved = map_saved
    node.pre_return_graph_saved = graph_saved
    node.operation_deadline = time.time()-1
    node._safety_tick()
    node._fail_mission.assert_called_once()
    node._hold_motion.assert_called_once()
    node._queue_reachable_dock_selection.assert_not_called()


def test_old_checkpoint_callback_cannot_start_a_new_missions_return():
    node = mission_node()
    node._queue_reachable_dock_selection = MagicMock()
    node._on_pre_return_map_saved(MagicMock(), 6)
    node._queue_reachable_dock_selection.assert_not_called()


def test_arrival_uses_map_sealed_before_return(tmp_path):
    node = mission_node()
    node.map_save_dir = str(tmp_path)
    node.pre_return_map_saved = True
    node.pre_return_graph_saved = True
    node.pre_return_map_base = str(tmp_path/"sealed")
    node._save_final_map = MagicMock()
    node._write_session_metadata = MagicMock()
    node._pause_slam_after_completion = MagicMock()
    node._initiate_map_finalization(at_dock=True)
    node._save_final_map.assert_not_called()
    node._write_session_metadata.assert_called_once()
    node._pause_slam_after_completion.assert_called_once()
    assert node.final_map_base == str(tmp_path/"sealed")


def test_completed_map_pauses_slam_before_reporting_completion():
    node = mission_node()
    node.state = "FINALIZING_MAP"
    node.pause_slam_client = MagicMock()
    pause_future = MagicMock()
    node.pause_slam_client.call_async.return_value = pause_future
    node._complete_mapping = MagicMock()
    with patch.object(explorer_module, "Pause", NS(Request=lambda: NS()), create=True):
        node._pause_slam_after_completion()
    node._complete_mapping.assert_not_called()
    callback = pause_future.add_done_callback.call_args.args[0]
    response = MagicMock()
    response.result.return_value.status = True
    callback(response)
    assert node.slam_measurements_paused
    node._complete_mapping.assert_called_once()


def test_saved_map_load_uses_cubeys_pose2d_service_schema():
    class DeserializeRequest:
        START_AT_FIRST_NODE = 1

        def __init__(self):
            self.filename = ""
            self.match_type = 0
            self.initial_pose = NS(x=0.0, y=0.0, theta=0.0)

    node = mission_node()
    node.state = "LOADING_MAP"
    node.deserialize_client = MagicMock()
    operation = MagicMock()
    node.deserialize_client.call_async.return_value = operation
    pause_future = MagicMock()
    pause_future.result.return_value.status = True

    with patch.object(
        explorer_module,
        "DeserializePoseGraph",
        NS(Request=DeserializeRequest, Response=NS(RESULT_SUCCESS=0)),
        create=True,
    ):
        node._on_slam_paused_for_map_load(pause_future, "/maps/saved", "saved", 7)

    request = node.deserialize_client.call_async.call_args.args[0]
    assert request.filename == "/maps/saved"
    assert request.match_type == DeserializeRequest.START_AT_FIRST_NODE
    assert request.initial_pose.theta == 0.0
    operation.add_done_callback.assert_called_once()


def test_saved_map_schema_error_cannot_crash_navigation_supervisor():
    class InvalidRequest:
        START_AT_FIRST_NODE = 1

        def __init__(self):
            raise AttributeError("unexpected service schema")

    node = mission_node()
    node.state = "LOADING_MAP"
    node.deserialize_client = MagicMock()
    node._fail_mission = MagicMock()
    pause_future = MagicMock()
    pause_future.result.return_value.status = True

    with patch.object(
        explorer_module,
        "DeserializePoseGraph",
        NS(Request=InvalidRequest, Response=NS(RESULT_SUCCESS=0)),
        create=True,
    ):
        node._on_slam_paused_for_map_load(pause_future, "/maps/saved", "saved", 7)

    node._fail_mission.assert_called_once()
    node.deserialize_client.call_async.assert_not_called()


def test_pose_export_expires_independently_of_static_map(tmp_path):
    path = tmp_path/"pose.json"
    path.write_text(json.dumps({"timestamp": 100, "pose_fresh": True, "pose": {"theta_deg": 90}, "imu_ok": True}))
    assert read_live_pose(path, now=100.1)["pose"]["theta_deg"] == 90
    assert not read_live_pose(path, now=100.6)["pose_fresh"]
    assert not read_live_pose(path, now=99)["pose_fresh"]


def test_acknowledgement_requires_current_mission_id():
    service = CubeyNavService()
    now = time.time()
    old = {"timestamp": now, "state": "EXPLORING", "mission_id": "previous"}
    with patch.object(service, "_read_ros2_status", return_value=old):
        assert not service._wait_for_ros2_state({"EXPLORING"}, now-1, timeout_s=0.01, mission_id="current")


def test_ekf_fuses_no_command_or_duplicate_orientation():
    from pathlib import Path
    config = yaml.safe_load(Path("ros2/config/ekf_params.yaml").read_text())["ekf_filter_node"]["ros__parameters"]
    assert config["odom0_config"] == [True, True]+[False]*13
    assert config["imu0_config"] == [False]*5+[True]+[False]*9
    assert config["use_control"] is False
    assert config["world_frame"] == "odom"
