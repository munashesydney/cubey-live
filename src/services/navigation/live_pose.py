"""Read the ROS pose independently of occupancy-map publication."""
import json
import time


def read_live_pose(path="/tmp/cubey_nav2_live_pose.json", now=None):
    now = time.time() if now is None else now
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not 0 <= now-float(data.get("timestamp", 0)) <= 0.5:
            return {"pose_fresh": False, "imu_ok": False}
        return data
    except (OSError, TypeError, ValueError):
        return {"pose_fresh": False, "imu_ok": False}
