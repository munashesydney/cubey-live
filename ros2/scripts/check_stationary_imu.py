#!/usr/bin/env python3
"""Read-only ROS probe. Never publishes motion or starts a mapping session."""
import argparse
import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu, LaserScan
from nav_msgs.msg import Odometry
from std_msgs.msg import String


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()
    rclpy.init()
    node = Node("cubey_stationary_imu_probe")
    samples = {topic: [] for topic in ("/imu/raw", "/imu/data", "/odom", "/scan")}
    statuses = {}
    subscriptions = []

    def record(topic, msg):
        stamp = msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
        row = {"stamp": stamp, "age": node.get_clock().now().nanoseconds/1e9-stamp}
        if topic.startswith("/imu/"):
            q = msg.orientation
            row["yaw"] = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
        elif topic == "/odom":
            p = msg.pose.pose.position
            row["position"] = [p.x, p.y]
        samples[topic].append(row)

    def status(topic, msg):
        statuses[topic] = json.loads(msg.data)

    for topic, kind in (("/imu/raw", Imu), ("/imu/data", Imu), ("/odom", Odometry), ("/scan", LaserScan)):
        subscriptions.append(node.create_subscription(kind, topic, lambda m, t=topic: record(t, m), qos_profile_sensor_data))
    for topic in ("/cubey/imu_status", "/cubey/odometry_status", "/cubey/exploration_status"):
        subscriptions.append(node.create_subscription(String, topic, lambda m, t=topic: status(t, m), 10))
    deadline = time.monotonic()+min(30.0, max(1.0, args.seconds))
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
        report = {"status": statuses, "topics": {}}
        for topic, rows in samples.items():
            result = {"samples": len(rows)}
            if len(rows) > 1:
                elapsed = rows[-1]["stamp"]-rows[0]["stamp"]
                result.update(hz=round((len(rows)-1)/elapsed, 2) if elapsed > 0 else None,
                              max_age_s=round(max(r["age"] for r in rows), 4),
                              nonincreasing_timestamps=sum(b["stamp"] <= a["stamp"] for a,b in zip(rows, rows[1:])))
                if "yaw" in rows[0]:
                    delta = [math.degrees(math.atan2(math.sin(r["yaw"]-rows[0]["yaw"]), math.cos(r["yaw"]-rows[0]["yaw"]))) for r in rows]
                    result["heading_span_deg"] = round(max(delta)-min(delta), 4)
                if "position" in rows[0]:
                    result["position_span_m"] = round(max(math.dist(r["position"], rows[0]["position"]) for r in rows), 4)
            report["topics"][topic] = result
        print(json.dumps(report, indent=2))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
