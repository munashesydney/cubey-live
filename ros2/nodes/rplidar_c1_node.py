#!/usr/bin/env python3
"""
Native RPLIDAR C1 ROS 2 Driver Node for Cubey.

Communicates directly with Slamtec RPLIDAR C1 over serial UART (/dev/ttyUSB0 @ 460800 baud)
using standard 5-byte sample packet decoding and publishes sensor_msgs/msg/LaserScan.
Bypasses legacy SDK express scan incompatibilities.
"""

import math
import struct
import sys
import threading
import time
from typing import List, Optional, Tuple

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import LaserScan, Imu
    from rclpy.time import Time
    from rclpy.qos import qos_profile_sensor_data
except ImportError:
    print("Warning: rclpy / sensor_msgs not found in host Python. Run within Pixi environment.", file=sys.stderr)
    Node = object

try:
    import serial
except ImportError:
    serial = None

try:
    from .imu_support import HeadingHistory, yaw
except ImportError:
    from imu_support import HeadingHistory, yaw


# Slamtec RPLIDAR Protocol Constants
SYNC_BYTE = 0xA5
SYNC_BYTE2 = 0x5A
CMD_STOP = 0x25
CMD_RESET = 0x40
CMD_SCAN = 0x20
RESP_DESCRIPTOR_LEN = 7


class RPLidarC1Node(Node):
    """Publishes ROS 2 LaserScan topics from RPLIDAR C1 360-degree sweeps."""

    def __init__(self):
        super().__init__("rplidar_c1_node")

        self.declare_parameter("serial_port", "/dev/ttyUSB0")
        self.declare_parameter("serial_baudrate", 460800)
        self.declare_parameter("frame_id", "laser")
        self.declare_parameter("min_range", 0.05)
        self.declare_parameter("max_range", 12.0)
        self.declare_parameter("angle_compensate", True)
        # A scan is only safe to treat as a common-time snapshot when the IMU
        # covers nearly all of the LiDAR revolution. Publishing a partly
        # deskewed scan is worse than dropping it: SLAM can turn its uncorrected
        # segment into a curved wall during a turn.
        self.declare_parameter("min_deskew_coverage", 0.98)

        self.port = self.get_parameter("serial_port").value
        self.baudrate = self.get_parameter("serial_baudrate").value
        self.frame_id = self.get_parameter("frame_id").value
        self.min_range = float(self.get_parameter("min_range").value)
        self.max_range = float(self.get_parameter("max_range").value)
        self.angle_compensate = bool(self.get_parameter("angle_compensate").value)
        self.min_deskew_coverage = max(
            0.0, min(1.0, float(self.get_parameter("min_deskew_coverage").value))
        )

        self.pub_scan = self.create_publisher(LaserScan, "/scan", 10)
        self.headings = HeadingHistory()
        self.sub_imu = self.create_subscription(Imu, "/imu/data", self._on_imu, qos_profile_sensor_data)

        self.deskew_dropped_scans = 0
        self.last_deskew_drop_reason = ""
        self._last_deskew_warning_at = 0.0

        self.serial_conn: Optional[serial.Serial] = None
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        self._start_lidar()

    def _on_imu(self, msg):
        q = msg.orientation
        self.headings.add(msg.header.stamp.sec+msg.header.stamp.nanosec/1e9,
                          yaw((q.x, q.y, q.z, q.w)))

    def _start_lidar(self):
        """Open serial connection and send START_SCAN command."""
        if serial is None:
            self.get_logger().error("pyserial is not available.")
            return

        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=1.0,
                write_timeout=1.0,
            )
            self.serial_conn.reset_input_buffer()
            self.serial_conn.reset_output_buffer()

            # Set DTR to enable motor if supported
            try:
                self.serial_conn.dtr = False
                time.sleep(0.05)
                self.serial_conn.dtr = True
            except Exception:
                pass

            # Send stop first
            self.serial_conn.write(bytearray([SYNC_BYTE, CMD_STOP]))
            time.sleep(0.05)
            self.serial_conn.reset_input_buffer()

            # Send start scan command (0xA5 0x20)
            self.serial_conn.write(bytearray([SYNC_BYTE, CMD_SCAN]))
            time.sleep(0.05)

            # Read 7-byte response descriptor
            desc = self.serial_conn.read(RESP_DESCRIPTOR_LEN)
            if len(desc) == RESP_DESCRIPTOR_LEN and desc[0] == SYNC_BYTE and desc[1] == SYNC_BYTE2:
                self.get_logger().info(f"RPLIDAR C1 connected on {self.port} @ {self.baudrate} baud. Scan descriptor OK.")
            else:
                self.get_logger().warn(f"RPLIDAR C1 descriptor skipped/non-standard on {self.port}, starting stream parse.")

            self._running = True
            self._worker_thread = threading.Thread(target=self._scan_loop, daemon=True)
            self._worker_thread.start()

        except Exception as e:
            self.get_logger().error(f"Failed to connect to RPLIDAR on {self.port}: {e}")

    def _scan_loop(self):
        """Continuous background loop parsing 5-byte sample nodes."""
        accumulated_points: List[Tuple[float, float, int, float]] = []
        last_sweep_time = time.time()
        NODE_LEN = 5

        while self._running and self.serial_conn and self.serial_conn.is_open:
            try:
                raw = self.serial_conn.read(NODE_LEN)
                if len(raw) < NODE_LEN:
                    continue

                b0, b1, b2, b3, b4 = raw[0], raw[1], raw[2], raw[3], raw[4]
                sync_bit = b0 & 0x01
                inv_sync_bit = (b0 >> 1) & 0x01
                check_bit = b1 & 0x01

                if check_bit != 1 or (sync_bit == inv_sync_bit):
                    # Packet alignment slip — read 1 byte to resynchronize
                    self.serial_conn.read(1)
                    continue

                quality = b0 >> 2
                angle_q6 = (b2 << 7) | (b1 >> 1)
                angle_deg = angle_q6 / 64.0
                if angle_deg >= 360.0:
                    angle_deg -= 360.0

                dist_q2 = (b4 << 8) | b3
                dist_m = (dist_q2 / 4.0) / 1000.0

                if sync_bit == 1 and len(accumulated_points) > 15:
                    now = time.time()
                    scan_time = now - last_sweep_time
                    last_sweep_time = now

                    self._publish_laser_scan(accumulated_points, scan_time)
                    accumulated_points = []

                if dist_m > 0:
                    # RPLIDAR C1: 0 deg = front, angles increase clockwise
                    # ROS REP 103: 0 rad = front, angles increase counter-clockwise, range [-pi, pi]
                    rad = -math.radians(angle_deg)
                    while rad <= -math.pi:
                        rad += 2.0 * math.pi
                    while rad > math.pi:
                        rad -= 2.0 * math.pi
                    accumulated_points.append((rad, dist_m, quality, self.get_clock().now().nanoseconds/1e9))

            except Exception as e:
                if self._running:
                    self.get_logger().error(f"RPLIDAR read error: {e}")
                    time.sleep(0.1)
                break

    def _drop_unsafe_scan(self, reason: str, point_count: int, covered_count: int = 0) -> None:
        """Record a scan deliberately withheld from every downstream consumer."""
        self.deskew_dropped_scans += 1
        self.last_deskew_drop_reason = reason
        now = time.monotonic()
        # Keep the diagnostic useful without flooding the ROS log at scan rate.
        if now - self._last_deskew_warning_at >= 1.0:
            self.get_logger().warn(
                "SCAN_DROPPED_UNSAFE_DESKEW "
                f"reason={reason} covered={covered_count}/{point_count} "
                f"required={self.min_deskew_coverage:.0%}"
            )
            self._last_deskew_warning_at = now

    def _publish_laser_scan(self, points: List[Tuple[float, float, int, float]], scan_time: float):
        """Constructs and publishes sensor_msgs/msg/LaserScan message."""
        if not points:
            return

        num_readings = 360
        angle_min = -math.pi
        angle_increment = 2*math.pi / num_readings
        angle_max = angle_min+(num_readings-1)*angle_increment
        # Rebinning reverses acquisition order. Deskew rotations into the last
        # beam's frame and publish a common-time snapshot, not fictitious beam times.
        beam_headings = self.headings.at_many([point[3] for point in points])
        covered_count = sum(heading is not None for heading in beam_headings)
        if covered_count == 0:
            self._drop_unsafe_scan("missing_reference_heading", len(points))
            return

        # Do not fall back to raw beam coordinates when IMU interpolation is
        # incomplete. A whole dropped scan creates a small coverage gap; a
        # partial raw scan creates a persistent, bent obstacle in /map.
        coverage = covered_count / len(points)
        if coverage < self.min_deskew_coverage:
            self._drop_unsafe_scan("insufficient_heading_coverage", len(points), covered_count)
            return

        reference_index = next(
            index for index in range(len(beam_headings)-1, -1, -1)
            if beam_headings[index] is not None
        )
        stamp = points[reference_index][3]
        reference_yaw = beam_headings[reference_index]
        deskewed_points = []
        for (angle_rad, dist_m, quality, _), beam_yaw in zip(points, beam_headings):
            if beam_yaw is None:
                continue
            delta = beam_yaw-reference_yaw
            bx = dist_m*math.cos(angle_rad)-0.035
            by = dist_m*math.sin(angle_rad)
            lx = bx*math.cos(delta)-by*math.sin(delta)+0.035
            ly = bx*math.sin(delta)+by*math.cos(delta)
            deskewed_points.append((math.atan2(ly, lx), math.hypot(lx, ly), quality))

        ranges = [float("inf")] * num_readings
        intensities = [0.0] * num_readings

        for angle_rad, dist_m, quality in deskewed_points:
            if self.min_range <= dist_m <= self.max_range:
                idx = int((angle_rad - angle_min) / angle_increment)
                if 0 <= idx < num_readings:
                    if dist_m < ranges[idx]:
                        ranges[idx] = dist_m
                        intensities[idx] = float(quality)

        msg = LaserScan()
        msg.header.stamp = Time(nanoseconds=int(stamp*1e9)).to_msg()
        msg.header.frame_id = self.frame_id
        msg.angle_min = angle_min
        msg.angle_max = angle_max
        msg.angle_increment = angle_increment
        # Points were explicitly deskewed into the final beam's frame, so this
        # is a true common-time scan rather than a scan that needs TF to infer
        # per-beam motion.
        msg.time_increment = 0.0
        msg.scan_time = scan_time if scan_time > 0 else 0.1
        msg.range_min = self.min_range
        msg.range_max = self.max_range
        msg.ranges = ranges
        msg.intensities = intensities

        self.pub_scan.publish(msg)


    def destroy_node(self):
        self._running = False
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.write(bytearray([SYNC_BYTE, CMD_STOP]))
                time.sleep(0.05)
                self.serial_conn.close()
            except Exception:
                pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RPLidarC1Node()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()

