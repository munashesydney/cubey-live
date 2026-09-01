#!/usr/bin/env python3
"""
cmd_vel to ESP32 Mecanum Serial Bridge Node for Cubey Robot.

Subscribes to ROS 2 /cmd_vel (geometry_msgs/msg/Twist), scales linear X/Y and angular Z
velocities to normalized [-1000..1000] integer space, and dispatches TWIST packets
to the ESP32 (cubey_wheels) over hardware UART (/dev/ttyAMA0 @ 115200 baud).
"""

from __future__ import annotations

import json
import logging
import os
import select
import socket
import sys
import threading
import time
from typing import Optional

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
except ImportError:
    print("Warning: rclpy / geometry_msgs not found in standard Python environment. Must be run in Pixi ROS 2 environment.", file=sys.stderr)
    Node = object

try:
    import serial
except ImportError:
    serial = None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [cmd_vel_bridge] %(message)s")
logger = logging.getLogger("cmd_vel_bridge")


def apply_minimum_effective_command(
    forward: int,
    left: int,
    counter_clockwise: int,
    minimum: int,
) -> tuple[int, int, int]:
    """Raise a nonzero command vector above the drivetrain's static-friction floor."""
    peak = max(abs(forward), abs(left), abs(counter_clockwise))
    minimum = max(0, min(1000, int(minimum)))
    if peak == 0 or minimum == 0 or peak >= minimum:
        return forward, left, counter_clockwise

    scale = minimum / peak
    return (
        int(round(forward * scale)),
        int(round(left * scale)),
        int(round(counter_clockwise * scale)),
    )


class CmdVelSerialBridgeNode(Node):
    """Bridges ROS 2 /cmd_vel velocity commands to Cubey's ESP32 mecanum controller."""

    def __init__(self):
        super().__init__("cubey_cmd_vel_bridge")

        # Declare parameters
        self.declare_parameter("serial_port", "/dev/ttyAMA0")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("max_linear_x_mps", 0.30)
        self.declare_parameter("max_linear_y_mps", 0.25)
        self.declare_parameter("max_angular_z_radps", 1.80)
        # The ESP32 runs the motors at speed 180/255. A normalized command of
        # 390 therefore produces about 70 PWM, the firmware's proven minimum
        # useful motor-test power. Lower values only make Cubey's motors buzz.
        self.declare_parameter("minimum_effective_command", 390)
        self.declare_parameter("command_timeout_sec", 0.40)
        self.declare_parameter("publish_rate_hz", 20.0)

        self.port = self.get_parameter("serial_port").value
        self.baudrate = self.get_parameter("baudrate").value
        self.max_vx = float(self.get_parameter("max_linear_x_mps").value)
        self.max_vy = float(self.get_parameter("max_linear_y_mps").value)
        self.max_wz = float(self.get_parameter("max_angular_z_radps").value)
        self.minimum_effective_command = int(
            self.get_parameter("minimum_effective_command").value
        )
        self.timeout_sec = float(self.get_parameter("command_timeout_sec").value)
        self.publish_rate = float(self.get_parameter("publish_rate_hz").value)

        self.serial_conn: Optional[serial.Serial] = None
        self._write_lock = threading.Lock()

        self.target_forward = 0
        self.target_left = 0
        self.target_ccw = 0
        self.last_cmd_time = time.time()
        self.is_active = False

        self._connect_serial()

        # Subscribe to /cmd_vel
        self.sub_cmd_vel = self.create_subscription(
            Twist,
            "/cmd_vel",
            self._on_cmd_vel,
            10
        )

        # High-rate dispatch timer (20 Hz)
        self.timer = self.create_timer(1.0 / self.publish_rate, self._dispatch_loop)

        # Background reader for ESP32 telemetry
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_telemetry_loop, daemon=True)
        self._reader_thread.start()

        # Background UDP listener for Python application layer (teleop, web joystick, Gemini voice)
        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._udp_sock.bind(("127.0.0.1", 9876))
            self._udp_sock.setblocking(False)
            self._udp_thread = threading.Thread(target=self._udp_listener_loop, daemon=True)
            self._udp_thread.start()
            self.get_logger().info("UDP IPC Command Listener active on 127.0.0.1:9876")
        except Exception as e:
            self.get_logger().warn(f"Could not bind UDP 9876: {e}")

        self.get_logger().info(f"Cubey cmd_vel Serial Bridge started on {self.port} @ {self.baudrate} baud")

    def _connect_serial(self):
        """Open serial port connection to ESP32."""
        if serial is None:
            self.get_logger().error("pyserial is not available.")
            return

        try:
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.1,
                write_timeout=0.1
            )
            self.get_logger().info(f"Connected to ESP32 UART: {self.port}")
        except Exception as e:
            self.get_logger().warn(f"Failed to open UART {self.port}: {e}. Retrying in background...")
            self.serial_conn = None

    def _on_cmd_vel(self, msg: Twist):
        """Handle incoming velocity command."""
        vx = msg.linear.x
        vy = msg.linear.y
        wz = msg.angular.z

        # Scale velocities to [-1000..1000] integer range
        fwd = int(max(-1.0, min(1.0, vx / self.max_vx)) * 1000) if self.max_vx > 0 else 0
        left = int(max(-1.0, min(1.0, vy / self.max_vy)) * 1000) if self.max_vy > 0 else 0
        ccw = int(max(-1.0, min(1.0, wz / self.max_wz)) * 1000) if self.max_wz > 0 else 0
        fwd, left, ccw = apply_minimum_effective_command(
            fwd,
            left,
            ccw,
            self.minimum_effective_command,
        )

        self.target_forward = fwd
        self.target_left = left
        self.target_ccw = ccw
        self.last_cmd_time = time.time()
        self.is_active = True

    def _dispatch_loop(self):
        """Periodically sends TWIST packets to ESP32 or auto-stops on timeout."""
        now = time.time()
        if now - self.last_cmd_time > self.timeout_sec:
            # Deadman timeout
            if self.is_active or self.target_forward != 0 or self.target_left != 0 or self.target_ccw != 0:
                self.target_forward = 0
                self.target_left = 0
                self.target_ccw = 0
                self.is_active = False
                self._send_raw("TWIST:0,0,0\n")
            return

        if self.is_active:
            packet = f"TWIST:{self.target_forward},{self.target_left},{self.target_ccw}\n"
            self._send_raw(packet)

    def _send_raw(self, packet: str):
        """Send raw line to ESP32 over serial."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        with self._write_lock:
            try:
                self.serial_conn.write(packet.encode("ascii"))
                self.serial_conn.flush()
            except Exception as e:
                self.get_logger().error(f"Serial write error: {e}")

    def _udp_listener_loop(self):
        """Listens for commands from wheels_service.py on localhost:9876."""
        while self._running:
            try:
                ready, _, _ = select.select([self._udp_sock], [], [], 0.5)
                if not ready:
                    continue
                data, _ = self._udp_sock.recvfrom(4096)
                if not data:
                    continue
                text = data.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue

                if text.startswith("{"):
                    try:
                        cmd = json.loads(text)
                        action = cmd.get("action")
                        speed = int(cmd.get("speed", 180))
                        norm = int(max(0, min(1000, (speed / 255.0) * 1000)))
                        if action == "forward":
                            self.target_forward = norm
                            self.target_left = 0
                            self.target_ccw = 0
                        elif action == "backward":
                            self.target_forward = -norm
                            self.target_left = 0
                            self.target_ccw = 0
                        elif action == "strafeLeft":
                            self.target_forward = 0
                            self.target_left = norm
                            self.target_ccw = 0
                        elif action == "strafeRight":
                            self.target_forward = 0
                            self.target_left = -norm
                            self.target_ccw = 0
                        elif action == "rotateLeft":
                            self.target_forward = 0
                            self.target_left = 0
                            self.target_ccw = norm
                        elif action == "rotateRight":
                            self.target_forward = 0
                            self.target_left = 0
                            self.target_ccw = -norm
                        elif action == "forwardLeft":
                            self.target_forward = norm
                            self.target_left = norm // 2
                            self.target_ccw = 0
                        elif action == "forwardRight":
                            self.target_forward = norm
                            self.target_left = -norm // 2
                            self.target_ccw = 0
                        elif action == "stop":
                            self.target_forward = 0
                            self.target_left = 0
                            self.target_ccw = 0
                        elif "vx" in cmd or "wz" in cmd:
                            vx = float(cmd.get("vx", 0.0))
                            vy = float(cmd.get("vy", 0.0))
                            wz = float(cmd.get("wz", 0.0))
                            self.target_forward = int(max(-1.0, min(1.0, vx / self.max_vx)) * 1000) if self.max_vx > 0 else 0
                            self.target_left = int(max(-1.0, min(1.0, vy / self.max_vy)) * 1000) if self.max_vy > 0 else 0
                            self.target_ccw = int(max(-1.0, min(1.0, wz / self.max_wz)) * 1000) if self.max_wz > 0 else 0
                        self.last_cmd_time = time.time()
                        self.is_active = True
                    except Exception:
                        pass
                else:
                    # Raw ASCII line (e.g. "CMD:forward", "CMD:stop", "SPEED:180", "PING")
                    if text.lower() == "cmd:stop":
                        self.target_forward = 0
                        self.target_left = 0
                        self.target_ccw = 0
                        self.last_cmd_time = time.time()
                        self.is_active = True
                        self._send_raw("TWIST:0,0,0\n")
                    self._send_raw(f"{text}\n")
            except Exception:
                time.sleep(0.05)

    def _read_telemetry_loop(self):
        """Reads incoming telemetry from ESP32."""
        tmp_telemetry = "/tmp/cubey_wheels_telemetry.txt"
        tmp_w = "/tmp/cubey_wheels_telemetry.txt.tmp"
        while self._running:
            if not self.serial_conn or not self.serial_conn.is_open:
                time.sleep(1.0)
                self._connect_serial()
                continue
            try:
                line = self.serial_conn.readline().decode("utf-8", errors="ignore").strip()
                if line:
                    try:
                        with open(tmp_w, "w") as f:
                            f.write(line + "\n")
                        os.replace(tmp_w, tmp_telemetry)
                    except Exception:
                        pass
            except Exception:
                time.sleep(0.1)

    def destroy_node(self):
        self._running = False
        if hasattr(self, "_udp_sock") and self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self._send_raw("TWIST:0,0,0\nCMD:stop\n")
                self.serial_conn.close()
            except Exception:
                pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CmdVelSerialBridgeNode()
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

