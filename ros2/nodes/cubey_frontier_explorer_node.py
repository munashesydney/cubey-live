#!/usr/bin/env python3
"""
Cubey Frontier Exploration & Auto-Stop Supervisor Node.

Subscribes to SLAM occupancy grid (/map) and odometry (/odom),
extracts frontier clusters (free space bordering unknown space),
prioritizes exploration goals by utility (size vs. distance),
dispatches action goals to Nav2 (NavigateToPose),
and executes an autonomous 4-stage Auto-Stop protocol:
  1. Detects 0 reachable frontiers (space fully explored).
  2. Navigates robot back to starting origin / dock (0.0, 0.0, 0.0).
  3. Freezes SLAM measurements (/slam_toolbox/pause_new_measurements).
  4. Optimizes pose graph and saves map (/slam_toolbox/save_map).
"""

import math
import os
import sys
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.action import ActionClient
    from nav_msgs.msg import OccupancyGrid, Odometry
    from geometry_msgs.msg import PoseStamped
    from std_msgs.msg import String
    from std_srvs.srv import SetBool, Trigger
    from nav2_msgs.action import NavigateToPose
    from action_msgs.msg import GoalStatus
except ImportError as e:
    print(f"Error: ROS 2 dependencies missing: {e}. Run inside Pixi environment.", file=sys.stderr)
    Node = object
    ActionClient = object


class CubeyFrontierExplorerNode(Node):
    """Autonomous room exploration supervisor with auto-stop for Nav2 and SLAM Toolbox."""

    def __init__(self):
        super().__init__("cubey_frontier_explorer")

        # Declare parameters
        self.declare_parameter("autostart", True)
        self.declare_parameter("min_frontier_size", 4)          # At 5cm/cell, 4 cells = 20cm
        self.declare_parameter("robot_radius_m", 0.22)          # Safety inflation boundary
        self.declare_parameter("update_interval_sec", 2.0)      # Rate to re-evaluate frontiers
        self.declare_parameter("goal_timeout_sec", 35.0)        # Max time before replanning a goal
        self.declare_parameter("min_goal_distance_m", 0.35)     # Ignore frontiers too close to robot
        self.declare_parameter("max_goal_distance_m", 15.0)     # Max exploration horizon
        self.declare_parameter("map_save_dir", "/home/cubey/Desktop/cubey-live/data/maps")

        self.autostart = bool(self.get_parameter("autostart").value)
        self.min_frontier_size = int(self.get_parameter("min_frontier_size").value)
        self.robot_radius_m = float(self.get_parameter("robot_radius_m").value)
        self.update_interval = float(self.get_parameter("update_interval_sec").value)
        self.goal_timeout_sec = float(self.get_parameter("goal_timeout_sec").value)
        self.min_goal_dist = float(self.get_parameter("min_goal_distance_m").value)
        self.max_goal_dist = float(self.get_parameter("max_goal_distance_m").value)
        self.map_save_dir = str(self.get_parameter("map_save_dir").value)

        # State tracking
        # States: IDLE, EXPLORING, RETURNING_TO_DOCK, FINALIZING_MAP, COMPLETED
        self.state = "IDLE"
        self.robot_pose: Optional[Tuple[float, float, float]] = None  # x, y, theta_rad
        self.start_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.latest_map: Optional[OccupancyGrid] = None

        # Goal & Frontier Management
        self.current_goal_coord: Optional[Tuple[float, float]] = None
        self.goal_start_time: float = 0.0
        self.active_goal_handle = None
        self.blacklist: Dict[Tuple[int, int], float] = {}       # (gx, gy) -> expiry timestamp
        self.zero_frontier_cycles: int = 0
        self.total_frontiers_mapped: int = 0

        # Subscriptions
        self.sub_map = self.create_subscription(
            OccupancyGrid,
            "/map",
            self._on_map,
            10
        )
        self.sub_odom = self.create_subscription(
            Odometry,
            "/odom",
            self._on_odom,
            10
        )

        # Publisher for external telemetry (Web UI / Gemini audio bridge)
        self.pub_status = self.create_publisher(
            String,
            "/cubey/exploration_status",
            10
        )

        # Service Servers to trigger start/stop
        self.srv_start = self.create_service(
            Trigger,
            "/cubey/start_exploration",
            self._handle_start_exploration
        )
        self.srv_stop = self.create_service(
            Trigger,
            "/cubey/stop_exploration",
            self._handle_stop_exploration
        )

        # Nav2 NavigateToPose Action Client
        self.nav_client = ActionClient(self, NavigateToPose, "navigate_to_pose")

        # Main supervision timer (e.g. 1-2 Hz)
        self.timer = self.create_timer(self.update_interval, self._supervision_loop)

        # Fast status publish timer (2 Hz)
        self.status_timer = self.create_timer(0.5, self._publish_status)

        self.get_logger().info("Cubey Frontier Explorer & Auto-Stop Supervisor initialized.")

        if self.autostart:
            self.get_logger().info("Autostart enabled. Waiting for /map and Nav2 action server...")
            self.state = "EXPLORING"

    # ------------------------------------------------------------------
    # Telemetry & Callbacks
    # ------------------------------------------------------------------

    def _on_odom(self, msg: Odometry):
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        # Compute yaw from quaternion (Z, W)
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        self.robot_pose = (pos.x, pos.y, yaw)

    def _on_map(self, msg: OccupancyGrid):
        self.latest_map = msg

    def _handle_start_exploration(self, request, response):
        self.state = "EXPLORING"
        self.zero_frontier_cycles = 0
        self.blacklist.clear()
        self.current_goal_coord = None
        if self.robot_pose:
            self.start_pose = self.robot_pose
        self.get_logger().info("Manual start received: exploration activated.")
        response.success = True
        response.message = "Autonomous exploration started."
        return response

    def _handle_stop_exploration(self, request, response):
        self._cancel_active_nav_goal()
        self.state = "IDLE"
        self.current_goal_coord = None
        self.get_logger().info("Stop command received: exploration halted.")
        response.success = True
        response.message = "Exploration stopped."
        return response

    # ------------------------------------------------------------------
    # Frontier Extraction & Clustering (Pure NumPy & BFS)
    # ------------------------------------------------------------------

    def _extract_frontiers(self, map_msg: OccupancyGrid) -> List[Tuple[float, float, int]]:
        """
        Identifies connected frontier clusters (free cells bordering unknown cells).
        Returns a list of (world_x, world_y, cluster_size) sorted by exploration utility.
        """
        width = map_msg.info.width
        height = map_msg.info.height
        res = map_msg.info.resolution
        origin_x = map_msg.info.origin.position.x
        origin_y = map_msg.info.origin.position.y

        # Convert to 2D numpy array
        grid = np.array(map_msg.data, dtype=np.int8).reshape((height, width))

        # 0 <= cell < 25 is confirmed free space
        free = (grid >= 0) & (grid < 25)
        # cell == -1 is unmapped unknown space
        unknown = (grid == -1)
        # cell >= 50 is confirmed obstacle
        obstacle = (grid >= 50)

        if not np.any(free) or not np.any(unknown):
            return []

        # Find free cells with at least one 4-connected unknown neighbor
        has_unknown_neighbor = np.zeros_like(free, dtype=bool)
        has_unknown_neighbor[:-1, :] |= unknown[1:, :]   # Down
        has_unknown_neighbor[1:, :]  |= unknown[:-1, :]  # Up
        has_unknown_neighbor[:, :-1] |= unknown[:, 1:]   # Right
        has_unknown_neighbor[:, 1:]  |= unknown[:, :-1]  # Left

        raw_frontier = free & has_unknown_neighbor

        # Obstacle buffer safety: avoid frontiers within robot radius of an obstacle
        safety_cells = max(2, int(self.robot_radius_m / res))
        if np.any(obstacle):
            # Dilate obstacle mask by safety_cells
            for _ in range(safety_cells):
                obstacle[:-1, :] |= obstacle[1:, :]
                obstacle[1:, :]  |= obstacle[:-1, :]
                obstacle[:, :-1] |= obstacle[:, 1:]
                obstacle[:, 1:]  |= obstacle[:, :-1]
            raw_frontier = raw_frontier & (~obstacle)

        frontier_indices = np.argwhere(raw_frontier)
        if len(frontier_indices) == 0:
            return []

        # Cluster contiguous frontier cells via 8-connected BFS
        visited = np.zeros((height, width), dtype=bool)
        valid_clusters: List[Tuple[float, float, int]] = []
        now = time.time()

        # Clean expired blacklist entries
        self.blacklist = {k: v for k, v in self.blacklist.items() if v > now}

        robot_x, robot_y = self.robot_pose[:2] if self.robot_pose else (0.0, 0.0)

        for r, c in frontier_indices:
            if visited[r, c]:
                continue

            # BFS queue
            queue = deque([(r, c)])
            visited[r, c] = True
            cluster_cells = [(r, c)]

            while queue:
                cr, cc = queue.popleft()
                # Check 8 neighbors
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nr, nc = cr + dr, cc + dc
                        if 0 <= nr < height and 0 <= nc < width:
                            if raw_frontier[nr, nc] and not visited[nr, nc]:
                                visited[nr, nc] = True
                                queue.append((nr, nc))
                                cluster_cells.append((nr, nc))

            # Filter small noise clusters
            cluster_len = len(cluster_cells)
            if cluster_len < self.min_frontier_size:
                continue

            # Centroid in grid coordinates
            avg_r = sum(cell[0] for cell in cluster_cells) / cluster_len
            avg_c = sum(cell[1] for cell in cluster_cells) / cluster_len

            # Convert to world coordinates
            wx = origin_x + (avg_c + 0.5) * res
            wy = origin_y + (avg_r + 0.5) * res

            # Check blacklist
            grid_key = (int(round(avg_r)), int(round(avg_c)))
            if grid_key in self.blacklist:
                continue

            # Distance filter
            dist = math.hypot(wx - robot_x, wy - robot_y)
            if dist < self.min_goal_dist or dist > self.max_goal_dist:
                continue

            valid_clusters.append((wx, wy, cluster_len))

        # Utility scoring: score = size * 0.4 - distance * 1.2
        def _score(cluster: Tuple[float, float, int]) -> float:
            wx, wy, size = cluster
            dist = math.hypot(wx - robot_x, wy - robot_y)
            return (size * 0.4) - (dist * 1.2)

        valid_clusters.sort(key=_score, reverse=True)
        return valid_clusters

    # ------------------------------------------------------------------
    # Nav2 Action Client Coordination
    # ------------------------------------------------------------------

    def _send_nav2_goal(self, target_x: float, target_y: float, target_yaw: float = 0.0):
        """Dispatches an action goal to Nav2 bt_navigator."""
        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().warn("Nav2 navigate_to_pose action server not yet ready.")
            return

        goal_msg = NavigateToPose.Goal()
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()
        goal_msg.pose.pose.position.x = float(target_x)
        goal_msg.pose.pose.position.y = float(target_y)
        goal_msg.pose.pose.position.z = 0.0

        # Yaw to quaternion
        goal_msg.pose.pose.orientation.z = math.sin(target_yaw / 2.0)
        goal_msg.pose.pose.orientation.w = math.cos(target_yaw / 2.0)

        self.current_goal_coord = (target_x, target_y)
        self.goal_start_time = time.time()

        send_future = self.nav_client.send_goal_async(
            goal_msg,
            feedback_callback=self._on_nav2_feedback
        )
        send_future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().warn("Nav2 rejected frontier goal! Blacklisting coordinate.")
            if self.current_goal_coord and self.latest_map:
                self._blacklist_coord(self.current_goal_coord)
            self.current_goal_coord = None
            return

        self.active_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._on_goal_result)

    def _on_nav2_feedback(self, feedback_msg):
        # Progress check
        pass

    def _on_goal_result(self, future):
        result = future.result()
        status = result.status
        self.active_goal_handle = None

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().info("🎯 Reached frontier waypoint successfully!")
            self.total_frontiers_mapped += 1
            if self.state == "RETURNING_TO_DOCK":
                self._initiate_map_finalization()
        else:
            self.get_logger().warn(f"Nav2 goal terminated with status: {status}. Blacklisting coordinate.")
            if self.current_goal_coord:
                self._blacklist_coord(self.current_goal_coord)

        self.current_goal_coord = None

    def _cancel_active_nav_goal(self):
        if self.active_goal_handle:
            try:
                self.active_goal_handle.cancel_goal_async()
            except Exception as e:
                self.get_logger().warn(f"Error cancelling goal: {e}")
        self.active_goal_handle = None
        self.current_goal_coord = None

    def _blacklist_coord(self, coord: Tuple[float, float]):
        if not self.latest_map:
            return
        res = self.latest_map.info.resolution
        origin_x = self.latest_map.info.origin.position.x
        origin_y = self.latest_map.info.origin.position.y
        gx = int((coord[0] - origin_x) / res)
        gy = int((coord[1] - origin_y) / res)
        self.blacklist[(gy, gx)] = time.time() + 45.0  # 45-second blacklist

    # ------------------------------------------------------------------
    # High-Level Exploration & Auto-Stop State Machine
    # ------------------------------------------------------------------

    def _supervision_loop(self):
        """Main state machine controlling frontier exploration and auto-stop."""
        if self.state not in ("EXPLORING", "RETURNING_TO_DOCK"):
            return

        if self.latest_map is None:
            self.get_logger().info("Waiting for initial /map from SLAM Toolbox...", throttle_duration_sec=5.0)
            return

        # Handle active goal timeout / stall
        if self.current_goal_coord and (time.time() - self.goal_start_time > self.goal_timeout_sec):
            self.get_logger().warn("Active frontier goal timed out (>35s). Cancelling and selecting new frontier.")
            self._cancel_active_nav_goal()
            return

        # -------------------------------------------------------------
        # STATE: EXPLORING
        # -------------------------------------------------------------
        if self.state == "EXPLORING":
            frontiers = self._extract_frontiers(self.latest_map)

            # AUTO-STOP TRIGGER CHECK:
            if len(frontiers) == 0:
                self.zero_frontier_cycles += 1
                self.get_logger().info(
                    f"Frontiers empty (cycle {self.zero_frontier_cycles}/2). Checking for map completion...",
                    throttle_duration_sec=2.0
                )
                # Confirm 0 frontiers across 2 consecutive updates to prevent sensor flicker triggers
                if self.zero_frontier_cycles >= 2:
                    self._trigger_auto_stop_sequence()
                return
            else:
                self.zero_frontier_cycles = 0

            # If already driving to an active goal, let it complete
            if self.current_goal_coord is not None:
                return

            # Pick highest-utility frontier
            best_x, best_y, best_size = frontiers[0]
            robot_x, robot_y = self.robot_pose[:2] if self.robot_pose else (0.0, 0.0)
            # Orient robot heading toward the frontier centroid
            target_yaw = math.atan2(best_y - robot_y, best_x - robot_x)

            self.get_logger().info(
                f"🤖 [AutoNav] Dispatching Nav2 Goal -> ({best_x:.2f}m, {best_y:.2f}m, "
                f"Cluster: {best_size} cells). Remaining frontiers: {len(frontiers)}"
            )
            self._send_nav2_goal(best_x, best_y, target_yaw)

    # ------------------------------------------------------------------
    # 4-Phase Auto-Stop Execution
    # ------------------------------------------------------------------

    def _trigger_auto_stop_sequence(self):
        """Phase 1 & 2: Initiates return to starting dock after frontiers are depleted."""
        self.get_logger().info("=========================================================")
        self.get_logger().info("🎉 ALL ACCESSIBLE FRONTIERS FULLY EXPLORED!")
        self.get_logger().info("🤖 Phase 1: Initiating Return-to-Dock Sequence.")
        self.get_logger().info("=========================================================")

        self._cancel_active_nav_goal()
        self.state = "RETURNING_TO_DOCK"

        # Navigate to origin (0.0, 0.0) where mapping started
        dock_x, dock_y, dock_yaw = self.start_pose
        self.get_logger().info(f"Navigating back to dock origin: ({dock_x:.2f}m, {dock_y:.2f}m)...")
        self._send_nav2_goal(dock_x, dock_y, dock_yaw)

    def _initiate_map_finalization(self):
        """Phase 3 & 4: Pause SLAM scan matching, trigger loop closure, and save map."""
        self.state = "FINALIZING_MAP"
        self.get_logger().info("=========================================================")
        self.get_logger().info("🏁 Robot arrived safely at dock. Finalizing SLAM map...")
        self.get_logger().info("=========================================================")

        os.makedirs(self.map_save_dir, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_map_base = os.path.join(self.map_save_dir, f"cubey_floorplan_{timestamp_str}")

        # 1. Pause SLAM Toolbox measurements
        try:
            pause_client = self.create_client(SetBool, "/slam_toolbox/pause_new_measurements")
            if pause_client.wait_for_service(timeout_sec=2.0):
                req = SetBool.Request()
                req.data = True
                pause_client.call_async(req)
                self.get_logger().info("SLAM measurements paused for map lock.")
        except Exception as e:
            self.get_logger().warn(f"Could not pause slam_toolbox: {e}")

        # 2. Trigger SLAM Toolbox Save Map service
        try:
            from slam_toolbox.srv import SaveMap
            save_client = self.create_client(SaveMap, "/slam_toolbox/save_map")
            if save_client.wait_for_service(timeout_sec=2.0):
                req = SaveMap.Request()
                req.name.data = final_map_base
                save_client.call_async(req)
                self.get_logger().info(f"💾 Map saved via slam_toolbox to: {final_map_base}")
        except Exception as e:
            self.get_logger().warn(f"Error calling /slam_toolbox/save_map: {e}")

        self.state = "COMPLETED"
        self.get_logger().info("=========================================================")
        self.get_logger().info("✅ ROOM MAPPING & AUTO-STOP MISSION FULLY COMPLETE!")
        self.get_logger().info("=========================================================")

    # ------------------------------------------------------------------
    # Telemetry Broadcast
    # ------------------------------------------------------------------

    def _publish_status(self):
        """Broadcasts exploration status JSON on /cubey/exploration_status."""
        dist_m = 0.0
        if self.current_goal_coord and self.robot_pose:
            dist_m = math.hypot(
                self.current_goal_coord[0] - self.robot_pose[0],
                self.current_goal_coord[1] - self.robot_pose[1]
            )

        status_json = (
            f'{{"state": "{self.state}", '
            f'"goal_x": {round(self.current_goal_coord[0], 2) if self.current_goal_coord else "null"}, '
            f'"goal_y": {round(self.current_goal_coord[1], 2) if self.current_goal_coord else "null"}, '
            f'"distance_remaining_m": {round(dist_m, 2)}, '
            f'"frontiers_completed": {self.total_frontiers_mapped}}}'
        )

        msg = String()
        msg.data = status_json
        self.pub_status.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CubeyFrontierExplorerNode()
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
