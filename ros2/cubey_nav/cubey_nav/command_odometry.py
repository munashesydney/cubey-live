"""Conservative interim odometry from the motion Cubey actually executes."""

import math
from typing import Optional

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_ros import TransformBroadcaster


class CommandOdometry(Node):
    """Integrate executed body velocity until measured wheel odometry exists."""

    def __init__(self) -> None:
        super().__init__("command_odometry")
        self.declare_parameter("command_timeout_s", 0.55)
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")

        self._timeout_s = float(self.get_parameter("command_timeout_s").value)
        self._odom_frame = str(self.get_parameter("odom_frame").value)
        self._base_frame = str(self.get_parameter("base_frame").value)
        self._velocity = Twist()
        self._last_command_ns: Optional[int] = None
        self._last_update_ns = self.get_clock().now().nanoseconds
        self._x = 0.0
        self._y = 0.0
        self._yaw = 0.0

        self.create_subscription(
            Twist,
            "/cubey/executed_cmd_vel",
            self._on_velocity,
            20,
        )
        self._odom_pub = self.create_publisher(Odometry, "/odom", 20)
        self._tf = TransformBroadcaster(self)
        self.create_timer(0.02, self._update)

    def _on_velocity(self, message: Twist) -> None:
        self._velocity = message
        self._last_command_ns = self.get_clock().now().nanoseconds

    def _update(self) -> None:
        now = self.get_clock().now()
        now_ns = now.nanoseconds
        dt = max(0.0, min(0.10, (now_ns - self._last_update_ns) / 1e9))
        self._last_update_ns = now_ns

        fresh = (
            self._last_command_ns is not None
            and (now_ns - self._last_command_ns) / 1e9 <= self._timeout_s
        )
        vx = self._velocity.linear.x if fresh else 0.0
        vy = self._velocity.linear.y if fresh else 0.0
        wz = self._velocity.angular.z if fresh else 0.0

        self._x += (vx * math.cos(self._yaw) - vy * math.sin(self._yaw)) * dt
        self._y += (vx * math.sin(self._yaw) + vy * math.cos(self._yaw)) * dt
        self._yaw = math.atan2(
            math.sin(self._yaw + wz * dt),
            math.cos(self._yaw + wz * dt),
        )

        half_yaw = self._yaw * 0.5
        qz = math.sin(half_yaw)
        qw = math.cos(half_yaw)

        transform = TransformStamped()
        transform.header.stamp = now.to_msg()
        transform.header.frame_id = self._odom_frame
        transform.child_frame_id = self._base_frame
        transform.transform.translation.x = self._x
        transform.transform.translation.y = self._y
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw
        self._tf.sendTransform(transform)

        odometry = Odometry()
        odometry.header = transform.header
        odometry.child_frame_id = self._base_frame
        odometry.pose.pose.position.x = self._x
        odometry.pose.pose.position.y = self._y
        odometry.pose.pose.orientation.z = qz
        odometry.pose.pose.orientation.w = qw
        odometry.twist.twist.linear.x = vx
        odometry.twist.twist.linear.y = vy
        odometry.twist.twist.angular.z = wz
        # Command integration is deliberately reported with high uncertainty.
        odometry.pose.covariance[0] = 0.20
        odometry.pose.covariance[7] = 0.20
        odometry.pose.covariance[35] = 0.35
        odometry.twist.covariance[0] = 0.12
        odometry.twist.covariance[7] = 0.12
        odometry.twist.covariance[35] = 0.20
        self._odom_pub.publish(odometry)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CommandOdometry()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
