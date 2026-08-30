#!/usr/bin/env python3
"""
2D LiDAR Scan-Matching & Command Odometry Node for Cubey Robot.

Generates continuous /odom topic and 'odom' -> 'base_footprint' coordinate transform
by fusing 2D laser scan matching with open-loop velocity commands.
Compensates for the absence of hardware wheel encoders and IMU.
"""

import math
import sys
import time
from typing import List, Optional, Tuple

import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import LaserScan
    from geometry_msgs.msg import Twist, TransformStamped
    from nav_msgs.msg import Odometry
    from tf2_ros import TransformBroadcaster
except ImportError:
    print("Warning: rclpy / ros_msgs not available in host Python. Run inside Pixi environment.", file=sys.stderr)
    Node = object


def quaternion_from_euler(ai: float, aj: float, ak: float) -> Tuple[float, float, float, float]:
    """Calculate quaternion (x, y, z, w) from euler roll, pitch, yaw."""
    ai /= 2.0
    aj /= 2.0
    ak /= 2.0
    ci = math.cos(ai)
    si = math.sin(ai)
    cj = math.cos(aj)
    sj = math.sin(aj)
    ck = math.cos(ak)
    sk = math.sin(ak)
    cc = ci * ck
    cs = ci * sk
    sc = si * ck
    ss = si * sk

    x = cj * sc - sj * cs
    y = cj * ss + sj * cc
    z = cj * cs - sj * sc
    w = cj * cc + sj * ss
    return x, y, z, w


class CubeyOdometryNode(Node):
    """Computes high-frequency odometry from 2D LiDAR scans and motor commands."""

    def __init__(self):
        super().__init__("cubey_odometry_node")

        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("publish_tf", True)
        self.declare_parameter("freq", 15.0)

        self.odom_frame = self.get_parameter("odom_frame").value
        self.base_frame = self.get_parameter("base_frame").value
        self.publish_tf = bool(self.get_parameter("publish_tf").value)
        self.freq = float(self.get_parameter("freq").value)

        # Odometry State
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self.last_update_time = self.get_clock().now()

        # Scan Matching state
        self.prev_points: Optional[np.ndarray] = None
        self.last_scan_time = 0.0

        # ROS 2 Publishers & Broadcasters
        self.pub_odom = self.create_publisher(Odometry, "/odom", 10)
        self.tf_broadcaster = TransformBroadcaster(self) if self.publish_tf else None

        # Subscriptions
        self.sub_scan = self.create_subscription(LaserScan, "/scan", self._on_laser_scan, 5)
        self.sub_cmd = self.create_subscription(Twist, "/cmd_vel", self._on_cmd_vel, 10)

        # Periodic publish timer
        self.timer = self.create_timer(1.0 / self.freq, self._publish_odometry)

        self.get_logger().info(f"Cubey Odometry Node initialized. Publishing '{self.odom_frame}' -> '{self.base_frame}' @ {self.freq} Hz")

    def _on_cmd_vel(self, msg: Twist):
        """Update current estimated velocity from commanded motion."""
        self.vx = msg.linear.x
        self.vy = msg.linear.y
        self.wz = msg.angular.z

    def _on_laser_scan(self, msg: LaserScan):
        """Correlate consecutive scans to refine displacement (X, Y, Yaw)."""
        now_time = time.time()
        dt = now_time - self.last_scan_time if self.last_scan_time > 0 else 0.1
        self.last_scan_time = now_time

        ranges = np.array(msg.ranges, dtype=np.float32)
        angles = msg.angle_min + np.arange(len(ranges)) * msg.angle_increment

        # Filter valid ranges (0.15m to 12.0m)
        valid = (ranges >= 0.15) & (ranges <= 10.0) & np.isfinite(ranges)
        if np.count_nonzero(valid) < 30:
            return

        valid_ranges = ranges[valid]
        valid_angles = angles[valid]

        # Convert to local Cartesian points
        lx = valid_ranges * np.cos(valid_angles)
        ly = valid_ranges * np.sin(valid_angles)
        current_points = np.column_stack((lx, ly))

        # Downsample for fast correlation
        if len(current_points) > 80:
            step = len(current_points) // 80
            current_points = current_points[::step]

        if self.prev_points is not None and dt < 0.5:
            # Simple ICP / Point-to-Point correlation search centered around dead-reckoned motion
            dx_est = (self.vx * math.cos(self.yaw) - self.vy * math.sin(self.yaw)) * dt
            dy_est = (self.vx * math.sin(self.yaw) + self.vy * math.cos(self.yaw)) * dt
            dyaw_est = self.wz * dt

            # Refine pose delta using scan matching
            dx_match, dy_match, dyaw_match = self._correlate_scans(
                self.prev_points, current_points, dx_est, dy_est, dyaw_est
            )

            # Update pose
            self.x += dx_match
            self.y += dy_match
            self.yaw += dyaw_match
            # Normalize yaw to [-pi, pi]
            self.yaw = math.atan2(math.sin(self.yaw), math.cos(self.yaw))
        else:
            # First frame dead reckoning
            pass

        self.prev_points = current_points

    def _correlate_scans(
        self, prev_pts: np.ndarray, curr_pts: np.ndarray,
        dx_init: float, dy_init: float, dyaw_init: float
    ) -> Tuple[float, float, float]:
        """Fast grid-search scan match around initial motion estimate."""
        best_score = float("inf")
        best_dx = dx_init
        best_dy = dy_init
        best_dyaw = dyaw_init

        # Local search window
        dx_candidates = [dx_init - 0.03, dx_init, dx_init + 0.03]
        dy_candidates = [dy_init - 0.03, dy_init, dy_init + 0.03]
        dyaw_candidates = [dyaw_init - 0.04, dyaw_init, dyaw_init + 0.04]

        for dyaw in dyaw_candidates:
            cos_a = math.cos(dyaw)
            sin_a = math.sin(dyaw)
            # Rotate current points by candidate yaw
            rotated_x = curr_pts[:, 0] * cos_a - curr_pts[:, 1] * sin_a
            rotated_y = curr_pts[:, 0] * sin_a + curr_pts[:, 1] * cos_a

            for dx in dx_candidates:
                for dy in dy_candidates:
                    tx = rotated_x + dx
                    ty = rotated_y + dy

                    # Sum of nearest distances to previous points (approximate fast score)
                    # Sample subset for speed
                    dists_sq = np.min(
                        (prev_pts[:, 0, None] - tx[None, :]) ** 2 +
                        (prev_pts[:, 1, None] - ty[None, :]) ** 2,
                        axis=0
                    )
                    score = np.mean(dists_sq)

                    if score < best_score:
                        best_score = score
                        best_dx = dx
                        best_dy = dy
                        best_dyaw = dyaw

        return best_dx, best_dy, best_dyaw

    def _publish_odometry(self):
        """Periodically broadcast /odom message and TF transform."""
        now = self.get_clock().now()
        dt = (now - self.last_update_time).nanoseconds / 1e9
        self.last_update_time = now

        # Integrate velocity if no recent scan update occurred
        if dt > 0 and dt < 0.2:
            self.x += (self.vx * math.cos(self.yaw) - self.vy * math.sin(self.yaw)) * dt
            self.y += (self.vx * math.sin(self.yaw) + self.vy * math.cos(self.yaw)) * dt
            self.yaw += self.wz * dt
            self.yaw = math.atan2(math.sin(self.yaw), math.cos(self.yaw))

        qx, qy, qz, qw = quaternion_from_euler(0, 0, self.yaw)

        # 1. Publish Odometry Message
        odom_msg = Odometry()
        odom_msg.header.stamp = now.to_msg()
        odom_msg.header.frame_id = self.odom_frame
        odom_msg.child_frame_id = self.base_frame

        odom_msg.pose.pose.position.x = float(self.x)
        odom_msg.pose.pose.position.y = float(self.y)
        odom_msg.pose.pose.position.z = 0.0
        odom_msg.pose.pose.orientation.x = qx
        odom_msg.pose.pose.orientation.y = qy
        odom_msg.pose.pose.orientation.z = qz
        odom_msg.pose.pose.orientation.w = qw

        odom_msg.twist.twist.linear.x = float(self.vx)
        odom_msg.twist.twist.linear.y = float(self.vy)
        odom_msg.twist.twist.angular.z = float(self.wz)

        self.pub_odom.publish(odom_msg)

        # 2. Publish TF Transform: odom -> base_footprint
        if self.tf_broadcaster:
            t = TransformStamped()
            t.header.stamp = now.to_msg()
            t.header.frame_id = self.odom_frame
            t.child_frame_id = self.base_frame

            t.transform.translation.x = float(self.x)
            t.transform.translation.y = float(self.y)
            t.transform.translation.z = 0.0
            t.transform.rotation.x = qx
            t.transform.rotation.y = qy
            t.transform.rotation.z = qz
            t.transform.rotation.w = qw

            self.tf_broadcaster.sendTransform(t)


def main(args=None):
    rclpy.init(args=args)
    node = CubeyOdometryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
