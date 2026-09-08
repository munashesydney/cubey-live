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
    quaternion_multiply, quaternion_from_euler, match_translation, yaw, wrap)
from ros2.nodes import cubey_odometry_node as odometry
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
    node.mission_generation = 7
    node.state = "RETURNING_TO_DOCK"
    node.start_pose = (1., 2., 0.5)
    node.robot_pose = node.start_pose
    node.home_captured = True
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


def test_saved_checkpoint_starts_return_without_pausing_slam():
    node = mission_node()
    node.pre_return_map_base = "checkpoint"
    node._queue_reachable_dock_selection = MagicMock()
    result = MagicMock()
    result.result.return_value.result = 0
    with patch.object(explorer_module, "SaveMap", NS(Response=NS(RESULT_SUCCESS=0)), create=True):
        node._on_pre_return_map_saved(result, 7)
    node._queue_reachable_dock_selection.assert_called_once()
    assert node.pre_return_map_saved


def test_old_checkpoint_callback_cannot_start_a_new_missions_return():
    node = mission_node()
    node._queue_reachable_dock_selection = MagicMock()
    node._on_pre_return_map_saved(MagicMock(), 6)
    node._queue_reachable_dock_selection.assert_not_called()


def test_arrival_saves_final_map_even_when_a_checkpoint_exists(tmp_path):
    node = mission_node()
    node.map_save_dir = str(tmp_path)
    node.pre_return_map_saved = True
    node._save_final_map = MagicMock()
    node._complete_mapping = MagicMock()
    node._initiate_map_finalization(at_dock=True)
    node._save_final_map.assert_called_once()
    node._complete_mapping.assert_not_called()


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
