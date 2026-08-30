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
    from sensor_msgs.msg import LaserScan
except ImportError:
    print("Warning: rclpy / sensor_msgs not found in host Python. Run within Pixi environment.", file=sys.stderr)
    Node = object

try:
    import serial
except ImportError:
    serial = None


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

        self.port = self.get_parameter("serial_port").value
        self.baudrate = self.get_parameter("serial_baudrate").value
        self.frame_id = self.get_parameter("frame_id").value
        self.min_range = float(self.get_parameter("min_range").value)
        self.max_range = float(self.get_parameter("max_range").value)
        self.angle_compensate = bool(self.get_parameter("angle_compensate").value)

        self.pub_scan = self.create_publisher(LaserScan, "/scan", 10)

        self.serial_conn: Optional[serial.Serial] = None
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        self._start_lidar()

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
        accumulated_points: List[Tuple[float, float, int]] = []  # (angle_rad, distance_m, quality)
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
                    # In ROS standard frame: angle 0 = front (+X), angle counter-clockwise
                    # RPLIDAR C1 outputs clockwise angles starting from front notch.
                    # Convert to ROS standard REP 103 (counter-clockwise rads):
                    rad = math.radians(360.0 - angle_deg) if angle_deg > 0 else 0.0
                    accumulated_points.append((rad, dist_m, quality))

            except Exception as e:
                if self._running:
                    self.get_logger().error(f"RPLIDAR read error: {e}")
                    time.sleep(0.1)
                break

    def _publish_laser_scan(self, points: List[Tuple[float, float, int]], scan_time: float):
        """Constructs and publishes sensor_msgs/msg/LaserScan message."""
        if not points:
            return

        # Sort points by angle
        points.sort(key=lambda p: p[0])

        num_readings = 360
        angle_min = 0.0
        angle_max = 2.0 * math.pi
        angle_increment = (angle_max - angle_min) / num_readings

        ranges = [float("inf")] * num_readings
        intensities = [0.0] * num_readings

        for angle_rad, dist_m, quality in points:
            if self.min_range <= dist_m <= self.max_range:
                idx = int(angle_rad / angle_increment)
                if 0 <= idx < num_readings:
                    if dist_m < ranges[idx]:
                        ranges[idx] = dist_m
                        intensities[idx] = float(quality)

        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.angle_min = angle_min
        msg.angle_max = angle_max
        msg.angle_increment = angle_increment
        msg.time_increment = (scan_time / num_readings) if scan_time > 0 else 0.0
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

