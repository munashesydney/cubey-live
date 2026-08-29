"""Bounded frontier-goal coordinator; Nav2 owns all motion and recovery."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid
from rclpy.action import ActionClient
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.time import Time
from std_msgs.msg import Bool, String
from tf2_ros import Buffer, TransformException, TransformListener

from cubey_nav.frontier_planner import GridMap, select_frontier_goal


class FrontierExplorer(Node):
    def __init__(self) -> None:
        super().__init__("frontier_explorer")
        self.declare_parameter("enabled_on_start", False)
        self.declare_parameter("map_timeout_s", 5.0)
        self.declare_parameter("goal_blacklist_s", 90.0)
        self.declare_parameter("max_consecutive_failures", 5)
        self.declare_parameter("minimum_cluster_cells", 8)
        self.declare_parameter("clearance_m", 0.24)
        self.declare_parameter("standoff_m", 0.28)

        self._enabled = bool(self.get_parameter("enabled_on_start").value)
        self._map_timeout_s = float(self.get_parameter("map_timeout_s").value)
        self._blacklist_s = float(self.get_parameter("goal_blacklist_s").value)
        self._max_failures = int(
            self.get_parameter("max_consecutive_failures").value
        )
        self._minimum_cluster = int(
            self.get_parameter("minimum_cluster_cells").value
        )
        self._clearance_m = float(self.get_parameter("clearance_m").value)
        self._standoff_m = float(self.get_parameter("standoff_m").value)

        self._map: OccupancyGrid | None = None
        self._map_received_at = 0.0
        self._goal_handle = None
        self._goal = None
        self._blacklist: list[tuple[float, float, float]] = []
        self._consecutive_failures = 0
        self._empty_frontier_cycles = 0
        self._state = "WAITING_FOR_ENABLE"
        self._reason = "exploration_disabled"
        self._last_distance_remaining = None

        self._tf_buffer = Buffer(cache_time=Duration(seconds=30.0))
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._nav = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._status_pub = self.create_publisher(
            String, "/cubey/exploration/status", 10
        )
        self._goal_pub = self.create_publisher(
            PoseStamped, "/cubey/exploration/goal", 10
        )
        self.create_subscription(OccupancyGrid, "/map", self._on_map, 10)
        self.create_subscription(
            Bool, "/cubey/exploration/enabled", self._on_enabled, 10
        )
        self.create_subscription(
            Bool, "/cubey/mapping/reset", self._on_reset, 10
        )
        self.create_timer(1.0, self._tick)

    def _on_map(self, message: OccupancyGrid) -> None:
        self._map = message
        self._map_received_at = time.monotonic()

    def _on_enabled(self, message: Bool) -> None:
        requested = bool(message.data)
        if requested == self._enabled:
            return
        self._enabled = requested
        if not requested:
            self._state = "PAUSED"
            self._reason = "operator_paused"
            if self._goal_handle is not None:
                self._goal_handle.cancel_goal_async()
        else:
            self._consecutive_failures = 0
            self._empty_frontier_cycles = 0
            self._state = "WAITING_FOR_NAV2"
            self._reason = "operator_enabled"

    def _on_reset(self, message: Bool) -> None:
        if not message.data:
            return
        if self._goal_handle is not None:
            self._goal_handle.cancel_goal_async()
        self._goal_handle = None
        self._goal = None
        self._map = None
        self._map_received_at = 0.0
        self._blacklist = []
        self._consecutive_failures = 0
        self._empty_frontier_cycles = 0
        self._last_distance_remaining = None
        self._state = "WAITING_FOR_MAP" if self._enabled else "PAUSED"
        self._reason = "mapping_reset"

    def _tick(self) -> None:
        self._expire_blacklist()
        self._publish_status()
        if not self._enabled or self._goal_handle is not None:
            return
        if self._map is None or time.monotonic() - self._map_received_at > self._map_timeout_s:
            self._state = "WAITING_FOR_MAP"
            self._reason = "map_missing_or_stale"
            return
        if not self._nav.server_is_ready():
            self._state = "WAITING_FOR_NAV2"
            self._reason = "navigate_to_pose_unavailable"
            return

        try:
            transform = self._tf_buffer.lookup_transform(
                "map", "base_footprint", Time(), timeout=Duration(seconds=0.25)
            )
        except TransformException as exc:
            self._state = "WAITING_FOR_LOCALIZATION"
            self._reason = str(exc)
            return

        robot_x = transform.transform.translation.x
        robot_y = transform.transform.translation.y
        map_message = self._map
        grid = GridMap(
            width=map_message.info.width,
            height=map_message.info.height,
            resolution=map_message.info.resolution,
            origin_x=map_message.info.origin.position.x,
            origin_y=map_message.info.origin.position.y,
            data=map_message.data,
        )
        goal = select_frontier_goal(
            grid,
            robot_x,
            robot_y,
            excluded_world=((item[0], item[1]) for item in self._blacklist),
            minimum_cluster_cells=self._minimum_cluster,
            clearance_m=self._clearance_m,
            standoff_m=self._standoff_m,
        )
        if goal is None:
            self._empty_frontier_cycles += 1
            if self._empty_frontier_cycles >= 3:
                self._state = "COMPLETED"
                self._reason = "no_reachable_frontiers"
                self._enabled = False
            else:
                self._state = "SEARCHING"
                self._reason = "no_candidate_this_cycle"
            return

        self._empty_frontier_cycles = 0
        yaw = math.atan2(goal.y - robot_y, goal.x - robot_x)
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x = goal.x
        pose.pose.position.y = goal.y
        pose.pose.orientation.z = math.sin(yaw * 0.5)
        pose.pose.orientation.w = math.cos(yaw * 0.5)
        self._goal = goal
        self._goal_pub.publish(pose)

        request = NavigateToPose.Goal()
        request.pose = pose
        self._state = "GOAL_REQUESTED"
        self._reason = "frontier_selected"
        future = self._nav.send_goal_async(
            request, feedback_callback=self._on_feedback
        )
        future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future) -> None:
        try:
            goal_handle = future.result()
        except Exception as exc:
            self._record_failure(f"goal_request_error:{exc}")
            return
        if not goal_handle.accepted:
            self._record_failure("goal_rejected")
            return
        self._goal_handle = goal_handle
        self._state = "NAVIGATING"
        self._reason = "nav2_goal_active"
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._on_result)

    def _on_feedback(self, message) -> None:
        self._last_distance_remaining = round(
            float(message.feedback.distance_remaining), 3
        )

    def _on_result(self, future) -> None:
        try:
            wrapped_result = future.result()
            status = int(wrapped_result.status)
        except Exception as exc:
            self._record_failure(f"goal_result_error:{exc}")
            return
        finally:
            self._goal_handle = None

        if status == GoalStatus.STATUS_SUCCEEDED:
            self._state = "FRONTIER_REACHED"
            self._reason = "nav2_succeeded"
            self._consecutive_failures = 0
            self._goal = None
            return
        if status == GoalStatus.STATUS_CANCELED and not self._enabled:
            self._state = "PAUSED"
            self._reason = "operator_paused"
            self._goal = None
            return
        self._record_failure(f"nav2_result_status:{status}")

    def _record_failure(self, reason: str) -> None:
        if self._goal is not None:
            self._blacklist.append(
                (self._goal.x, self._goal.y, time.monotonic() + self._blacklist_s)
            )
        self._goal = None
        self._goal_handle = None
        self._consecutive_failures += 1
        self._state = "RECOVERING"
        self._reason = reason
        if self._consecutive_failures >= self._max_failures:
            self._enabled = False
            self._state = "FAULT"
            self._reason = "bounded_frontier_failures_exhausted"

    def _expire_blacklist(self) -> None:
        now = time.monotonic()
        self._blacklist = [item for item in self._blacklist if item[2] > now]

    def _publish_status(self) -> None:
        payload = {
            "enabled": self._enabled,
            "state": self._state,
            "reason": self._reason,
            "consecutive_failures": self._consecutive_failures,
            "blacklisted_goals": len(self._blacklist),
            "distance_remaining_m": self._last_distance_remaining,
            "goal": asdict(self._goal) if self._goal is not None else None,
        }
        message = String()
        message.data = json.dumps(payload, separators=(",", ":"))
        self._status_pub.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FrontierExplorer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
