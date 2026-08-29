"""Publish map-frame pose and trajectory in rosbridge-friendly messages."""

from __future__ import annotations

import math

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Path
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import Bool
from tf2_ros import Buffer, TransformException, TransformListener


class PoseRelay(Node):
    def __init__(self) -> None:
        super().__init__("pose_relay")
        self._buffer = Buffer(cache_time=Duration(seconds=30.0))
        self._listener = TransformListener(self._buffer, self)
        self._pose_pub = self.create_publisher(PoseStamped, "/cubey/pose", 10)
        self._path_pub = self.create_publisher(Path, "/cubey/trajectory", 10)
        self._path = Path()
        self._path.header.frame_id = "map"
        self._last_path_x = None
        self._last_path_y = None
        self.create_subscription(
            Bool, "/cubey/mapping/reset", self._on_reset, 10
        )
        self.create_timer(0.10, self._publish_pose)

    def _on_reset(self, message: Bool) -> None:
        if not message.data:
            return
        self._path = Path()
        self._path.header.frame_id = "map"
        self._last_path_x = None
        self._last_path_y = None
        self._path_pub.publish(self._path)

    def _publish_pose(self) -> None:
        try:
            transform = self._buffer.lookup_transform(
                "map", "base_footprint", Time(), timeout=Duration(seconds=0.03)
            )
        except TransformException:
            return

        pose = PoseStamped()
        pose.header = transform.header
        pose.pose.position.x = transform.transform.translation.x
        pose.pose.position.y = transform.transform.translation.y
        pose.pose.position.z = transform.transform.translation.z
        pose.pose.orientation = transform.transform.rotation
        self._pose_pub.publish(pose)

        x = pose.pose.position.x
        y = pose.pose.position.y
        moved = (
            self._last_path_x is None
            or math.hypot(x - self._last_path_x, y - self._last_path_y) >= 0.03
        )
        if moved:
            self._path.poses.append(pose)
            # Bound memory and browser payloads to the most recent trajectory.
            if len(self._path.poses) > 3000:
                self._path.poses = self._path.poses[-3000:]
            self._last_path_x = x
            self._last_path_y = y
        self._path.header.stamp = pose.header.stamp
        self._path_pub.publish(self._path)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PoseRelay()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
