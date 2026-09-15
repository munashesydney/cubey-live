#!/usr/bin/env python3
"""
cmd_vel to ESP32 Mecanum Serial Bridge Node for Cubey Robot.

Subscribes to ROS 2 /cmd_vel (geometry_msgs/msg/Twist), scales linear X/Y and angular Z
velocities to normalized [-1000..1000] integer space, and dispatches TWIST packets
to the ESP32 (cubey_wheels) over hardware UART (/dev/ttyAMA0 @ 115200 baud).
"""

from __future__ import annotations

import json
import math
from collections import deque
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
    from sensor_msgs.msg import Imu
    from std_msgs.msg import String
    from rclpy.time import Time
    from rclpy.qos import qos_profile_sensor_data
except ImportError:
    print("Warning: rclpy / geometry_msgs not found in standard Python environment. Must be run in Pixi ROS 2 environment.", file=sys.stderr)
    Node = object

try:
    import serial
except ImportError:
    serial = None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] [cmd_vel_bridge] %(message)s")
logger = logging.getLogger("cmd_vel_bridge")

try:
    from .imu_support import ImuPacketClock
except ImportError:
    from imu_support import ImuPacketClock


def telemetry_imu_connected(line: str) -> Optional[bool]:
    """Read the ESP's BNO08x link flag when that firmware reports it.

    Returns None for firmware that predates the field, so an old image cannot
    be mistaken for a disconnected sensor.
    """
    marker = "imu_conn="
    index = line.find(marker)
    if index < 0:
        return None
    return line[index + len(marker):index + len(marker) + 1] == "1"


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


class MinimumEffectiveCommandPulseFilter:
    """Preserve weak command averages using short, motor-effective pulses."""

    def __init__(self, minimum: int, pulse_frames: int = 3):
        self.minimum = max(0, min(1000, int(minimum)))
        self.pulse_frames = max(1, int(pulse_frames))
        self._accumulator = 0
        self._pulse_frames_remaining = 0
        self._last_command: Optional[tuple[int, int, int]] = None

    def reset(self):
        self._accumulator = 0
        self._pulse_frames_remaining = 0
        self._last_command = None

    def apply(
        self,
        forward: int,
        left: int,
        counter_clockwise: int,
    ) -> tuple[int, int, int]:
        command = (forward, left, counter_clockwise)
        peak = max(abs(value) for value in command)

        if peak == 0:
            self.reset()
            return command

        if self.minimum == 0 or peak >= self.minimum:
            self.reset()
            return command

        # Do not spend accumulated demand in a newly reversed direction. This
        # matters when Nav2 crosses its target heading and changes turn sign.
        if self._last_command is not None:
            direction_dot = sum(
                previous * current
                for previous, current in zip(self._last_command, command)
            )
            if direction_dot <= 0:
                self._accumulator = 0
                self._pulse_frames_remaining = 0
        self._last_command = command

        # Integer pulse-density modulation: over time, the emitted command's
        # average equals Nav2's requested magnitude, while each nonzero pulse
        # reaches the drivetrain's usable static-friction threshold.
        self._accumulator += peak
        if self._pulse_frames_remaining > 0:
            self._pulse_frames_remaining -= 1
            return apply_minimum_effective_command(*command, self.minimum)

        pulse_threshold = self.minimum * self.pulse_frames
        if self._accumulator < pulse_threshold:
            return (0, 0, 0)

        self._accumulator -= pulse_threshold
        self._pulse_frames_remaining = self.pulse_frames - 1
        return apply_minimum_effective_command(*command, self.minimum)


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
        self.declare_parameter("minimum_effective_pulse_frames", 3)
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
        self.minimum_effective_pulse_frames = int(
            self.get_parameter("minimum_effective_pulse_frames").value
        )
        self.timeout_sec = float(self.get_parameter("command_timeout_sec").value)
        self.publish_rate = float(self.get_parameter("publish_rate_hz").value)

        self.serial_conn: Optional[serial.Serial] = None
        self._write_lock = threading.Lock()

        self.target_forward = 0
        self.target_left = 0
        self.target_ccw = 0
        self.command_filter = MinimumEffectiveCommandPulseFilter(
            self.minimum_effective_command,
            self.minimum_effective_pulse_frames,
        )
        self.last_cmd_time = time.time()
        self.is_active = False
        self.command_source = "ros"
        self.teleop_speed = 180
        self.motion_state = "PREPARING"
        self.motion_ready = False
        self.motion_status_time = 0.0
        self.imu_clock = ImuPacketClock()
        self.imu_lines = deque(maxlen=100)
        self.imu_last_sample = None
        self.firmware_imu_conn: Optional[bool] = None
        self.pub_imu = self.create_publisher(Imu, "/imu/raw", qos_profile_sensor_data)
        self.pub_imu_status = self.create_publisher(String, "/cubey/imu_status", 10)
        self.sub_motion = self.create_subscription(String, "/cubey/motion_status", self._on_motion_status, 10)
        self.imu_timer = self.create_timer(0.02, self._publish_imu)

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
        if not self._motion_allowed("ros"):
            return
        self.command_source = "ros"
        vx = msg.linear.x
        vy = msg.linear.y
        wz = msg.angular.z

        # Scale velocities to [-1000..1000] integer range
        fwd = int(max(-1.0, min(1.0, vx / self.max_vx)) * 1000) if self.max_vx > 0 else 0
        left = int(max(-1.0, min(1.0, vy / self.max_vy)) * 1000) if self.max_vy > 0 else 0
        ccw = int(max(-1.0, min(1.0, wz / self.max_wz)) * 1000) if self.max_wz > 0 else 0

        self.target_forward = fwd
        self.target_left = left
        self.target_ccw = ccw
        self.last_cmd_time = time.time()
        self.is_active = True

    def _dispatch_loop(self):
        """Periodically sends TWIST packets to ESP32 or auto-stops on timeout."""
        now = time.time()
        if not self._motion_allowed(self.command_source):
            self.target_forward = self.target_left = self.target_ccw = 0
            self.command_filter.reset()
            if self.is_active:
                self._send_raw("TWIST:0,0,0\n")
            self.is_active = False
            return
        if now - self.last_cmd_time > self.timeout_sec:
            # Deadman timeout
            if self.is_active or self.target_forward != 0 or self.target_left != 0 or self.target_ccw != 0:
                self.target_forward = 0
                self.target_left = 0
                self.target_ccw = 0
                self.command_filter.reset()
                self.is_active = False
                self._send_raw("TWIST:0,0,0\n")
            return

        if self.is_active:
            forward, left, ccw = self.command_filter.apply(
                self.target_forward,
                self.target_left,
                self.target_ccw,
            )
            packet = f"TWIST:{forward},{left},{ccw}\n"
            self._send_raw(packet)

    def _on_motion_status(self, msg):
        try:
            status = json.loads(msg.data)
            self.motion_state = status["state"]
            age = self.get_clock().now().nanoseconds/1e9-float(status.get("timestamp", 0))
            self.motion_ready = status.get("ready") is True and 0 <= age < 0.3
            self.motion_status_time = time.monotonic()
        except (ValueError, KeyError, TypeError):
            self.motion_ready = False

    def _motion_allowed(self, source):
        if not self.motion_ready or time.monotonic()-self.motion_status_time > 0.5:
            return False
        if source == "ros":
            return self.motion_state in (
                "EXPLORING", "NAVIGATING", "RETURNING_TO_DOCK",
                "RECOVERING_STUCK", "LOCALIZING_GLOBAL",
            )
        return self.motion_state in (
            "IDLE", "MANUAL", "LOCALIZED", "COMPLETED", "COMPLETED_AWAY_FROM_DOCK"
        )

    def _publish_imu(self):
        now = self.get_clock().now().nanoseconds/1e9
        while self.imu_lines:
            line, received, monotonic_received = self.imu_lines.popleft()
            sample = self.imu_clock.parse(line, received, monotonic_received)
            if sample is None or now-sample.stamp > 0.2:
                continue
            self.imu_last_sample = sample
            msg = Imu()
            msg.header.stamp = Time(nanoseconds=int(sample.stamp*1e9)).to_msg()
            msg.header.frame_id = "imu_link"
            msg.orientation.x, msg.orientation.y, msg.orientation.z, msg.orientation.w = sample.quaternion
            msg.orientation_covariance = [math.radians(3)**2, 0., 0., 0., math.radians(3)**2, 0., 0., 0., math.radians(3)**2]
            # The firmware sends game rotation vectors, not gyro/acceleration.
            msg.angular_velocity_covariance[0] = -1.0
            msg.linear_acceleration_covariance[0] = -1.0
            self.pub_imu.publish(msg)
        sample = self.imu_last_sample
        age = now-sample.stamp if sample else None
        # Distinguish "firmware never found the sensor" from "waiting for a
        # fresh sample", which otherwise look identical from /imu/raw silence.
        reason = self.imu_clock.reason
        if sample is None and self.firmware_imu_conn is False:
            reason = "ESP firmware reports the BNO08x IMU is not connected"
        status = String()
        status.data = json.dumps({"healthy": bool(sample and 0 <= age <= 0.2 and not reason),
                                  "age_s": age, "stream": (*self.imu_clock.stream, self.imu_clock.host_clock_generation) if self.imu_clock.stream else None,
                                  "sequence": self.imu_clock.sequence, "sensor_us": self.imu_clock.sensor_us,
                                  "last_published_stamp": sample.stamp if sample else None,
                                  "calibration": sample.calibration if sample else None,
                                  "firmware_imu_connected": self.firmware_imu_conn,
                                  "reason": reason, "timestamp": now})
        self.pub_imu_status.publish(status)

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

                if text.startswith("CMD:"):
                    text = json.dumps({"action": text[4:], "speed": self.teleop_speed})
                parsed = None
                if text.startswith("{"):
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        continue

                # Stop and diagnostics remain available even during sensor faults.
                is_stop = text.lower() == "estop" or (isinstance(parsed, dict) and parsed.get("action") == "stop")
                if is_stop:
                    self.target_forward = self.target_left = self.target_ccw = 0
                    self.command_filter.reset()
                    self.is_active = False
                    self._send_raw("TWIST:0,0,0\n")
                    if text.lower() == "estop":
                        self._send_raw("ESTOP\n")
                    continue
                if text.startswith("SPEED:"):
                    try:
                        self.teleop_speed = max(70, min(255, int(text[6:])))
                    except ValueError:
                        pass
                    continue
                if not is_stop and not self._motion_allowed("teleop"):
                    if text.upper() in ("PING", "STATUS", "IMU", "RESET_ESTOP"):
                        self._send_raw(text+"\n")
                    continue
                self.command_source = "teleop"

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
                            self.command_filter.reset()
                        elif "vx" in cmd or "wz" in cmd:
                            vx = float(cmd.get("vx", 0.0))
                            vy = float(cmd.get("vy", 0.0))
                            wz = float(cmd.get("wz", 0.0))
                            self.target_forward = int(max(-1.0, min(1.0, vx / self.max_vx)) * 1000) if self.max_vx > 0 else 0
                            self.target_left = int(max(-1.0, min(1.0, vy / self.max_vy)) * 1000) if self.max_vy > 0 else 0
                            self.target_ccw = int(max(-1.0, min(1.0, wz / self.max_wz)) * 1000) if self.max_wz > 0 else 0
                        else:
                            continue
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
                        self.command_filter.reset()
                        self.last_cmd_time = time.time()
                        self.is_active = True
                        self._send_raw("TWIST:0,0,0\n")
                    if text.upper() in ("PING", "IMU", "STATUS", "RESET_ESTOP"):
                        self._send_raw(f"{text}\n")
            except Exception:
                time.sleep(0.05)

    def _read_telemetry_loop(self):
        """Reads incoming telemetry from ESP32."""
        tmp_telemetry = "/tmp/cubey_wheels_telemetry.txt"
        tmp_w = "/tmp/cubey_wheels_telemetry.txt.tmp"
        buffer = b""
        while self._running:
            if not self.serial_conn or not self.serial_conn.is_open:
                time.sleep(1.0)
                self._connect_serial()
                buffer = b""
                continue
            try:
                buffer += self.serial_conn.read(min(4096, self.serial_conn.in_waiting or 1))
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("ascii", errors="ignore").strip()
                    if line.startswith("IMU:") and "t_us=" in line:
                        self.imu_lines.append((line, self.get_clock().now().nanoseconds/1e9, time.monotonic()))
                    elif line.startswith("TELEMETRY:"):
                        self.firmware_imu_conn = telemetry_imu_connected(line)
                        with open(tmp_w, "w") as f:
                            f.write(line + "\n")
                        os.replace(tmp_w, tmp_telemetry)
                    elif line.startswith(("CLIFF_SENSOR_", "[CLIFF DETECTED]")):
                        self.get_logger().warn(line)
                if len(buffer) > 4096:
                    buffer = b""  # Discard a damaged unterminated packet.
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

