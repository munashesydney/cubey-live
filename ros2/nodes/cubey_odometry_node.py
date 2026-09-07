#!/usr/bin/env python3
"""Measured IMU heading and laser translation. The EKF alone owns odom TF."""
from __future__ import annotations
import json
import math
import numpy as np

try:
    from .imu_support import (HeadingHistory, conjugate, match_translation,
                              quaternion_from_euler, quaternion_multiply, wrap, yaw)
except ImportError:
    from imu_support import (HeadingHistory, conjugate, match_translation,
                             quaternion_from_euler, quaternion_multiply, wrap, yaw)

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Imu, LaserScan
    from nav_msgs.msg import Odometry
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
except ImportError:
    Node = object


class CubeyOdometryNode(Node):
    def __init__(self):
        super().__init__("cubey_odometry_node")
        for key, default in {"imu_mount_roll": 0.0, "imu_mount_pitch": 0.0,
                             "imu_mount_yaw": 0.0, "laser_x": -0.035,
                             "laser_y": 0.0, "laser_yaw": 0.0}.items():
            self.declare_parameter(key, default)
        self.mount = quaternion_from_euler(*(float(self.get_parameter("imu_mount_"+axis).value)
                                            for axis in ("roll", "pitch", "yaw")))
        self.laser_x = float(self.get_parameter("laser_x").value)
        self.laser_y = float(self.get_parameter("laser_y").value)
        self.laser_yaw = float(self.get_parameter("laser_yaw").value)
        self.imu_stream = None
        self.imu_healthy = False
        self.imu_status_time = 0.0
        self.fault = ""
        self._reset_state()
        self.pub_imu = self.create_publisher(Imu, "/imu/data", qos_profile_sensor_data)
        self.pub_translation = self.create_publisher(Odometry, "/odom/lidar", 10)
        self.pub_status = self.create_publisher(String, "/cubey/odometry_status", 10)
        self.sub_imu = self.create_subscription(Imu, "/imu/raw", self._on_imu, qos_profile_sensor_data)
        self.sub_imu_status = self.create_subscription(String, "/cubey/imu_status", self._on_imu_status, 10)
        self.sub_scan = self.create_subscription(LaserScan, "/scan", self._on_laser_scan, qos_profile_sensor_data)
        self.reset_service = self.create_service(Trigger, "/cubey/reset_odometry", self._handle_reset_odometry)
        self.timer = self.create_timer(0.05, self._publish_status)

    def _now(self):
        return self.get_clock().now().nanoseconds/1e9

    def _reset_state(self):
        self.reset_time = self._now()
        self.heading_reference = None
        self.history = HeadingHistory()
        self.last_imu_time = self.last_scan_time = self.last_translation_time = 0.0
        self.prev_points = None
        self.prev_scan_yaw = 0.0
        self.x = self.y = self.wz = 0.0
        self.position_variance = 0.0001

    def _handle_reset_odometry(self, request, response):
        self._reset_state()
        self.fault = ""
        response.success = True
        response.message = "Measurement queues cleared; waiting for fresh IMU reference."
        return response

    def _on_imu_status(self, msg):
        try:
            data = json.loads(msg.data)
            stream = data.get("stream")
            if self.imu_stream is not None and stream != self.imu_stream:
                self.fault = "IMU restarted; start a new mapping session"
                self.prev_points = None
            self.imu_stream = stream
            self.imu_healthy = data.get("healthy") is True
            self.imu_status_time = self._now()
        except (ValueError, TypeError):
            self.imu_healthy = False

    def _on_imu(self, msg):
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec/1e9
        if self.fault or stamp <= self.reset_time or not 0 <= self._now()-stamp <= 0.2:
            return
        q = msg.orientation
        body = quaternion_multiply((q.x, q.y, q.z, q.w), conjugate(self.mount))
        heading = yaw(body)
        if self.heading_reference is None:
            self.heading_reference = heading
        heading = wrap(heading-self.heading_reference)
        if self.history.samples:
            before, previous = self.history.samples[-1]
            dt = stamp-before
            if dt <= 0:
                return
            self.wz = wrap(heading-previous)/dt
            if abs(self.wz) > 4.0:
                self.fault = "Implausible IMU heading jump; restart mapping"
                return
        if not self.history.add(stamp, heading):
            return
        self.last_imu_time = stamp
        planar = Imu()
        planar.header = msg.header
        planar.header.frame_id = "base_link"  # Mounting already applied above.
        planar.orientation.z = math.sin(heading/2)
        planar.orientation.w = math.cos(heading/2)
        planar.orientation_covariance = list(msg.orientation_covariance)
        planar.angular_velocity_covariance[0] = -1.0
        planar.linear_acceleration_covariance[0] = -1.0
        self.pub_imu.publish(planar)

    def _on_laser_scan(self, msg):
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec/1e9
        now = self._now()
        if not 0 <= now-stamp <= 0.25:
            return
        if stamp <= self.reset_time or self.fault:
            return
        heading = self.history.at(stamp)
        if heading is None:
            return
        ranges = np.asarray(msg.ranges, dtype=float)
        angles = msg.angle_min + np.arange(len(ranges))*msg.angle_increment + self.laser_yaw
        valid = np.isfinite(ranges) & (ranges >= max(0.15, msg.range_min)) & (ranges <= min(10.0, msg.range_max))
        if np.count_nonzero(valid) < 30:
            self.prev_points = None
            return
        points = np.column_stack((ranges[valid]*np.cos(angles[valid])+self.laser_x,
                                  ranges[valid]*np.sin(angles[valid])+self.laser_y))
        points = points[::max(1, math.ceil(len(points)/180))]
        dt = stamp-self.last_scan_time
        if dt <= 0:
            return
        if self.prev_points is not None and dt < 0.4:
            measured = match_translation(self.prev_points, points, wrap(heading-self.prev_scan_yaw),
                                         max_translation=0.6*dt+0.015)
            if measured is not None:
                dx, dy, variance = measured
                c, s = math.cos(self.prev_scan_yaw), math.sin(self.prev_scan_yaw)
                self.x += c*dx-s*dy
                self.y += s*dx+c*dy
                self.position_variance += variance
                odom = Odometry()
                odom.header = msg.header
                odom.header.frame_id = "odom"
                odom.child_frame_id = "base_link"
                odom.pose.pose.position.x, odom.pose.pose.position.y = self.x, self.y
                odom.pose.pose.orientation.w = 1.0
                odom.pose.covariance[0] = odom.pose.covariance[7] = self.position_variance
                for index in (14, 21, 28, 35):
                    odom.pose.covariance[index] = 1e6
                # Descriptive measured twist, not fused alongside its own pose.
                rotation = heading-self.prev_scan_yaw
                odom.twist.twist.linear.x = (dx*math.cos(rotation)+dy*math.sin(rotation))/dt
                odom.twist.twist.linear.y = (-dx*math.sin(rotation)+dy*math.cos(rotation))/dt
                odom.twist.covariance[0] = odom.twist.covariance[7] = variance/dt**2
                self.pub_translation.publish(odom)
                self.last_translation_time = stamp
        self.prev_points, self.prev_scan_yaw = points, heading
        self.last_scan_time = stamp

    def _publish_status(self):
        now = self._now()
        imu_ok = self.imu_healthy and now-self.imu_status_time < 0.3 and 0 <= now-self.last_imu_time <= 0.2
        scan_ok = 0 <= now-self.last_translation_time <= 0.5
        msg = String()
        msg.data = json.dumps({"ready": bool(imu_ok and scan_ok and not self.fault),
                               "imu_available": self.imu_healthy and now-self.imu_status_time < 0.3,
                               "imu_ok": imu_ok, "scan_ok": scan_ok,
                               "reason": self.fault or ("" if imu_ok and scan_ok else "Waiting for fresh IMU and observable LiDAR translation"),
                               "reset_time": self.reset_time, "timestamp": now})
        self.pub_status.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CubeyOdometryNode()
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
