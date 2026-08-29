"""Tests for the fail-closed host-to-ROS hardware boundary."""

import json
import math
import time
import unittest
import zlib
from unittest.mock import MagicMock

from src.services.lidar_service import LidarPoint, LidarScanData, LidarService
from src.services.ros_bridge_service import RosBridgeService
from src.services.wheels_service import TelemetryData, WheelsService


class FakeConnection:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(json.loads(message))


class RosBridgeServiceTests(unittest.TestCase):
    def setUp(self):
        self.lidar = LidarService(default_port="MOCK_SIMULATOR")
        self.wheels = WheelsService(default_port="MOCK_SIMULATOR")
        self.wheels.connect(port="MOCK_SIMULATOR")
        self.service = RosBridgeService(
            url="ws://127.0.0.1:9090",
            lidar_service=self.lidar,
            wheels_service=self.wheels,
        )
        self.connection = FakeConnection()
        self.service._connection = self.connection
        self.service._connected = True

    def tearDown(self):
        self.service.command_output_enabled = False
        self.service.stop()
        self.wheels.disconnect()
        self.lidar.disconnect()

    def test_scan_is_published_in_ros_counter_clockwise_coordinates(self):
        scan = LidarScanData(
            points=[LidarPoint(angle_deg=90.0, distance_mm=1000.0, quality=55)],
            timestamp=10.25,
            scan_rate_hz=10.0,
            point_count=1,
        )

        self.service._publish_scan(scan)

        payload = self.connection.messages[-1]
        self.assertEqual(payload["topic"], "/scan")
        message = payload["msg"]
        increment = message["angle_increment"]
        right_index = int(round(((-math.pi / 2.0) + math.pi) / increment))
        self.assertEqual(message["ranges"][right_index], 1.0)
        self.assertEqual(message["intensities"][right_index], 55.0)
        self.assertEqual(message["header"]["frame_id"], "laser")

    def test_ros_velocity_is_monitor_only_until_output_is_commissioned(self):
        self.wheels.send_twist_normalized = MagicMock(return_value=True)
        command = {
            "op": "publish",
            "topic": "/cmd_vel",
            "msg": {
                "linear": {"x": 0.18, "y": 0.0, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
        }

        self.service._handle_rosbridge_message(json.dumps(command))

        self.wheels.send_twist_normalized.assert_not_called()
        self.assertEqual(self.service.status()["last_command"]["linear_x"], 0.18)

    def test_commissioned_ros_velocity_preserves_holonomic_axis_ratios(self):
        self.service.command_output_enabled = True
        self.wheels.send_twist_normalized = MagicMock(return_value=True)
        command = {
            "op": "publish",
            "topic": "/cmd_vel",
            "msg": {
                "linear": {"x": 0.09, "y": -0.15, "z": 0.0},
                "angular": {"x": 0.0, "y": 0.0, "z": 0.35},
            },
        }

        self.service._handle_rosbridge_message(json.dumps(command))

        self.wheels.send_twist_normalized.assert_called_once_with(500, -1000, 500)

    def test_stale_ros_velocity_stops_once(self):
        self.service.command_output_enabled = True
        self.service._watchdog_stopped = False
        self.service._last_command_received_at = (
            time.monotonic() - self.service.command_timeout_s - 0.1
        )
        self.wheels.stop = MagicMock(return_value=True)

        self.service._enforce_command_watchdog()
        self.service._enforce_command_watchdog()

        self.wheels.stop.assert_called_once_with()
        self.assertTrue(self.service._watchdog_stopped)

    def test_discrete_manual_motion_is_reported_as_executed_velocity(self):
        velocity = self.service._velocity_from_telemetry(
            TelemetryData(motion="STRAFE_LEFT", speed=90)
        )

        self.assertEqual(velocity["linear_x"], 0.0)
        self.assertAlmostEqual(velocity["linear_y"], 0.15)
        self.assertEqual(velocity["angular_z"], 0.0)

    def test_ros_map_is_rotated_into_fixed_cubey_ui_coordinates(self):
        self.service._handle_map(
            {
                "info": {
                    "width": 2,
                    "height": 3,
                    "resolution": 0.5,
                    "origin": {"position": {"x": -1.0, "y": -2.0}},
                },
                "data": [0, 1, 2, 3, 4, -1],
            }
        )

        snapshot = self.service.mapping_snapshot()
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["width"], 3)
        self.assertEqual(snapshot["height"], 2)
        self.assertEqual(snapshot["origin_x_m"], 0.5)
        self.assertEqual(snapshot["origin_y_m"], -1.0)
        self.assertEqual(snapshot["total_explored_cells"], 5)
        self.assertEqual(
            list(zlib.decompress(self.service.get_compressed_grid())),
            [4, 2, 0, 255, 3, 1],
        )

    def test_ros_pose_is_converted_to_ui_position_and_clockwise_heading(self):
        half_sqrt = math.sqrt(0.5)
        self.service._handle_pose(
            {
                "pose": {
                    "position": {"x": 2.0, "y": 3.0},
                    "orientation": {"z": half_sqrt, "w": half_sqrt},
                }
            }
        )

        pose = self.service._pose
        self.assertEqual(pose["x_m"], -3.0)
        self.assertEqual(pose["y_m"], 2.0)
        self.assertEqual(pose["theta_deg"], 270.0)

    def test_exploration_status_and_mapping_switch_are_forwarded(self):
        status = {"enabled": True, "state": "NAVIGATING", "reason": "active"}
        self.service._handle_exploration_status({"data": json.dumps(status)})

        self.assertEqual(self.service.status()["exploration"], status)
        self.assertTrue(self.service.set_mapping_enabled(True))
        self.assertEqual(
            self.connection.messages[-1],
            {
                "op": "publish",
                "topic": "/cubey/exploration/enabled",
                "msg": {"data": True},
            },
        )


if __name__ == "__main__":
    unittest.main()
