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

import base64
import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time
import zlib
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
    from std_srvs.srv import Trigger
    from nav2_msgs.action import NavigateToPose
    from action_msgs.msg import GoalStatus
    from slam_toolbox.srv import Pause, SaveMap
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
        self.declare_parameter("min_frontier_size", 2)          # 2 cells = 10cm minimum frontier
        self.declare_parameter("robot_radius_m", 0.12)          # Obstacle buffer
        self.declare_parameter("update_interval_sec", 2.0)      # Rate to re-evaluate frontiers
        self.declare_parameter("goal_timeout_sec", 35.0)        # Max time before replanning a goal
        self.declare_parameter("min_goal_distance_m", 0.15)     # Accept close frontiers
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
        self.exploration_start_time: float = 0.0
        self.robot_pose: Optional[Tuple[float, float, float]] = None  # x, y, theta_rad
        self.start_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.latest_map: Optional[OccupancyGrid] = None
        self.trajectory: List[List[float]] = []

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
        self.pause_slam_client = self.create_client(Pause, "/slam_toolbox/pause_new_measurements")
        self.save_map_client = self.create_client(SaveMap, "/slam_toolbox/save_map")

        # Main supervision timer (e.g. 1-2 Hz)
        self.timer = self.create_timer(self.update_interval, self._supervision_loop)

        # Fast status publish timer (2 Hz)
        self.status_timer = self.create_timer(0.5, self._publish_status)

        # UDP only transports commands; ROS work is executed on the node thread.
        self._command_queue: queue.Queue[Dict[str, object]] = queue.Queue()
        self.command_timer = self.create_timer(0.1, self._process_pending_commands)

        # Background UDP listener for start/stop triggers from Python application layer
        self._running = True
        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._udp_sock.bind(("127.0.0.1", 9877))
            self._udp_sock.setblocking(False)
            self._udp_thread = threading.Thread(target=self._udp_trigger_loop, daemon=True)
            self._udp_thread.start()
            self.get_logger().info("Explorer UDP trigger listener active on 127.0.0.1:9877")
        except Exception as e:
            self.get_logger().warn(f"Could not bind UDP 9877: {e}")

        self.get_logger().info("Cubey Frontier Explorer & Auto-Stop Supervisor initialized.")

        if self.autostart:
            self.get_logger().info("Autostart enabled. Waiting for /map and Nav2 action server...")
            self.state = "EXPLORING"

    def _udp_trigger_loop(self):
        """Listens for {"command": "start"} or {"command": "stop"} on 127.0.0.1:9877."""
        while self._running:
            try:
                ready, _, _ = select.select([self._udp_sock], [], [], 0.5)
                if not ready:
                    continue
                data, _ = self._udp_sock.recvfrom(1024)
                if not data:
                    continue
                text = data.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                try:
                    command = json.loads(text) if text.startswith("{") else {"command": text.lower()}
                except (TypeError, ValueError):
                    continue
                if isinstance(command, dict):
                    self._command_queue.put(command)
            except Exception:
                time.sleep(0.05)

    def _process_pending_commands(self):
        """Execute app commands inside the rclpy executor thread."""
        while True:
            try:
                message = self._command_queue.get_nowait()
            except queue.Empty:
                return

            command = str(message.get("command", "")).lower()
            if command == "start":
                self._start_exploration()
            elif command == "stop":
                self._stop_exploration()
            elif command == "navigate":
                try:
                    x_m = float(message["x_m"])
                    y_m = float(message["y_m"])
                    theta_rad = math.radians(float(message.get("theta_deg", 0.0)))
                except (KeyError, TypeError, ValueError):
                    self.get_logger().warn("Ignored invalid NavigateToPose command from app.")
                    continue
                self._cancel_active_nav_goal()
                self.state = "NAVIGATING"
                self._send_nav2_goal(x_m, y_m, theta_rad)

    def _start_exploration(self):
        self._cancel_active_nav_goal()
        self.state = "EXPLORING"
        self.exploration_start_time = time.time()
        self.zero_frontier_cycles = 0
        self.total_frontiers_mapped = 0
        self.blacklist.clear()
        self.current_goal_coord = None
        self.trajectory = []
        if self.robot_pose:
            self.start_pose = self.robot_pose
        self.get_logger().info("Native Nav2 autonomous exploration started.")

    def _stop_exploration(self):
        self._cancel_active_nav_goal()
        self.state = "IDLE"
        self.current_goal_coord = None
        self.get_logger().info("Native Nav2 navigation stopped.")

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

        # Track trajectory for web canvas
        if not self.trajectory or math.hypot(pos.x - self.trajectory[-1][0], pos.y - self.trajectory[-1][1]) > 0.05:
            self.trajectory.append([round(pos.x, 3), round(pos.y, 3)])
            if len(self.trajectory) > 5000:
                self.trajectory = self.trajectory[-4000:]

    def _on_map(self, msg: OccupancyGrid):
        self.latest_map = msg

        # Export live map & pose directly for Cubey Web Panel Canvas
        try:
            raw_bytes = bytes(np.array(msg.data, dtype=np.int8))
            compressed = zlib.compress(raw_bytes, level=6)
            grid_b64 = base64.b64encode(compressed).decode("ascii")

            map_payload = {
                "grid_compressed_b64": grid_b64,
                "width": msg.info.width,
                "height": msg.info.height,
                "resolution_cm": round(msg.info.resolution * 100.0, 1),
                "origin_x_m": round(msg.info.origin.position.x, 3),
                "origin_y_m": round(msg.info.origin.position.y, 3),
                "pose": {
                    "x_m": round(self.robot_pose[0], 3) if self.robot_pose else 0.0,
                    "y_m": round(self.robot_pose[1], 3) if self.robot_pose else 0.0,
                    "theta_deg": round(math.degrees(self.robot_pose[2]), 1) if self.robot_pose else 0.0,
                },
                "trajectory": self.trajectory,
                "timestamp": time.time(),
            }
            tmp_map = "/tmp/cubey_nav2_live_map.json"
            tmp_w = "/tmp/cubey_nav2_live_map.json.tmp"
            with open(tmp_w, "w") as f:
                json.dump(map_payload, f)
            os.replace(tmp_w, tmp_map)
        except Exception:
            pass

    def _handle_start_exploration(self, request, response):
        self._start_exploration()
        response.success = True
        response.message = "Autonomous exploration started."
        return response

    def _handle_stop_exploration(self, request, response):
        self._stop_exploration()
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
        safety_cells = max(1, int(self.robot_radius_m / res))
        if np.any(obstacle):
            # Dilate obstacle mask by safety_cells (1-2 cells)
            for _ in range(min(2, safety_cells)):
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
            if self.state == "NAVIGATING":
                self.state = "ERROR"
            elif self.state == "RETURNING_TO_DOCK":
                self.get_logger().warn("Dock goal could not start; saving the map at the current safe position.")
                self._initiate_map_finalization()
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
            if self.state == "NAVIGATING":
                self.state = "BLOCKED"
            elif self.state == "RETURNING_TO_DOCK":
                self._initiate_map_finalization()
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
            if self.state == "RETURNING_TO_DOCK":
                self._initiate_map_finalization()
            elif self.state == "NAVIGATING":
                self.state = "REACHED"
            else:
                self.total_frontiers_mapped += 1
        else:
            self.get_logger().warn(f"Nav2 goal terminated with status: {status}. Blacklisting coordinate.")
            if self.current_goal_coord:
                self._blacklist_coord(self.current_goal_coord)
            if self.state == "RETURNING_TO_DOCK":
                self.get_logger().warn("Return-to-dock failed; saving the map at the current safe position.")
                self._initiate_map_finalization()
            elif self.state == "NAVIGATING":
                self.state = "BLOCKED"

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
            if self.state == "RETURNING_TO_DOCK":
                self.get_logger().warn("Return-to-dock timed out; saving the map at the current safe position.")
                self._initiate_map_finalization()
            elif self.state == "NAVIGATING":
                self.state = "BLOCKED"
            return

        # -------------------------------------------------------------
        # STATE: EXPLORING
        # -------------------------------------------------------------
        if self.state == "EXPLORING":
            frontiers = self._extract_frontiers(self.latest_map)

            # Check explored map extent
            grid_data = np.array(self.latest_map.data, dtype=np.int8)
            explored_cells = int(np.count_nonzero(grid_data != -1))
            time_exploring = (time.time() - self.exploration_start_time) if self.exploration_start_time > 0 else 0.0

            # AUTO-STOP TRIGGER CHECK:
            if len(frontiers) == 0:
                self.zero_frontier_cycles += 1
                self.get_logger().info(
                    f"Frontiers empty (cycle {self.zero_frontier_cycles}/4, explored={explored_cells}, elapsed={time_exploring:.1f}s)...",
                    throttle_duration_sec=2.0
                )
                # Only auto-stop if exploration has run for at least 30s AND mapped at least 250 cells (or completed frontiers)
                can_auto_stop = (time_exploring > 30.0 and (self.total_frontiers_mapped > 0 or explored_cells > 250))
                if can_auto_stop and self.zero_frontier_cycles >= 4:
                    self._trigger_auto_stop_sequence()
                elif self.zero_frontier_cycles >= 2 and self.current_goal_coord is None:
                    # Still in early exploration: survey surroundings by gently rotating to discover frontiers
                    self.get_logger().info("Surveying room with exploratory turn to discover frontiers...")
                    robot_x, robot_y = self.robot_pose[:2] if self.robot_pose else (0.0, 0.0)
                    robot_yaw = self.robot_pose[2] if self.robot_pose else 0.0
                    survey_yaw = robot_yaw + math.radians(90.0)
                    self._send_nav2_goal(robot_x, robot_y, survey_yaw)
                return
            else:
                self.zero_frontier_cycles = 0

            # If already driving to an active goal, let it complete
            if self.current_goal_coord is not None:
                return

            # Pick highest-utility frontier
            best_x, best_y, best_size = frontiers[0]
            robot_x, robot_y = self.robot_pose[:2] if self.robot_pose else (0.0, 0.0)

            # Pull goal 20cm back toward robot into confirmed free space
            dist_to_robot = math.hypot(best_x - robot_x, best_y - robot_y)
            if dist_to_robot > 0.35:
                pull_back = min(0.20, dist_to_robot * 0.35)
                nav_target_x = best_x - ((best_x - robot_x) / dist_to_robot) * pull_back
                nav_target_y = best_y - ((best_y - robot_y) / dist_to_robot) * pull_back
            else:
                nav_target_x, nav_target_y = best_x, best_y

            # Orient robot heading toward the frontier centroid
            target_yaw = math.atan2(best_y - robot_y, best_x - robot_x)

            self.get_logger().info(
                f"🤖 [AutoNav] Dispatching Nav2 Goal -> ({nav_target_x:.2f}m, {nav_target_y:.2f}m, "
                f"Frontier: [{best_x:.2f}, {best_y:.2f}], Cluster: {best_size} cells). Remaining frontiers: {len(frontiers)}"
            )
            self._send_nav2_goal(nav_target_x, nav_target_y, target_yaw)

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
        if self.state in ("FINALIZING_MAP", "COMPLETED"):
            return
        self.state = "FINALIZING_MAP"
        self.get_logger().info("=========================================================")
        self.get_logger().info("🏁 Robot arrived safely at dock. Finalizing SLAM map...")
        self.get_logger().info("=========================================================")

        os.makedirs(self.map_save_dir, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_map_base = os.path.join(self.map_save_dir, f"cubey_floorplan_{timestamp_str}")

        # Pause first, then save after the pause service responds.
        if self.pause_slam_client.wait_for_service(timeout_sec=2.0):
            pause_future = self.pause_slam_client.call_async(Pause.Request())
            pause_future.add_done_callback(lambda _: self._save_final_map(final_map_base))
            self.get_logger().info("SLAM pause requested for final map lock.")
        else:
            self.get_logger().warn("SLAM pause service unavailable; attempting map save anyway.")
            self._save_final_map(final_map_base)

    def _save_final_map(self, final_map_base: str):
        if not self.save_map_client.wait_for_service(timeout_sec=2.0):
            self.get_logger().error("SLAM save_map service unavailable; map remains available live but was not written to disk.")
            self._complete_mapping()
            return

        request = SaveMap.Request()
        request.name.data = final_map_base
        save_future = self.save_map_client.call_async(request)
        save_future.add_done_callback(lambda future: self._on_map_saved(future, final_map_base))

    def _on_map_saved(self, future, final_map_base: str):
        try:
            response = future.result()
            if response.result == SaveMap.Response.RESULT_SUCCESS:
                self.get_logger().info(f"💾 Map saved via SLAM Toolbox to: {final_map_base}")
            else:
                self.get_logger().error(f"SLAM Toolbox map save failed with result code {response.result}.")
        except Exception as e:
            self.get_logger().error(f"SLAM Toolbox map save failed: {e}")
        self._complete_mapping()

    def _complete_mapping(self):

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
            f'"frontiers_completed": {self.total_frontiers_mapped}, '
            f'"timestamp": {time.time()}}}'
        )

        msg = String()
        msg.data = status_json
        self.pub_status.publish(msg)

        # Fast atomic IPC file for Web UI & Gemini Voice Bridge
        try:
            tmp_file = "/tmp/cubey_exploration_status.json"
            tmp_write = "/tmp/cubey_exploration_status.json.tmp"
            with open(tmp_write, "w") as f:
                f.write(status_json)
            os.replace(tmp_write, tmp_file)
        except Exception:
            pass

    def destroy_node(self):
        self._running = False
        if hasattr(self, "_udp_sock") and self._udp_sock:
            try:
                self._udp_sock.close()
            except Exception:
                pass
        super().destroy_node()


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
