# Cubey Autonomous Navigation

## Safety status

The in-repository navigator is now a fail-closed transitional controller. It
will not intentionally move without fresh LiDAR scans and wheel-controller
telemetry, it monitors the complete commanded-motion corridor, bounds every
recovery rotation, detects lack of pose progress, and faults instead of retrying
forever.

Autonomous motion is disabled by default. `NAV_AUTONOMY_ENABLED=true` may only
be set after the following commissioning checks pass; mapping-only mode does
not require it.

This does **not** make the current hardware safety-rated. Do not run Cubey
unsupervised around stairs, children, pets, roads, or fragile objects. The
RPLIDAR C1 and host collision monitor are not certified safety devices.

## Mandatory commissioning before autonomous movement

1. Flash `cubey_wheels/cubey_wheels.ino`. The host and ESP32 must both support
   `ESTOP`, `RESET_ESTOP`, and `estop=` telemetry.
2. Measure the outer moving footprint and set `ROBOT_LENGTH_M` and
   `ROBOT_WIDTH_M`. Include protrusions, cable strain relief, and shell flex.
3. Measure the LiDAR mounting yaw. Set `LIDAR_MOUNT_YAW_DEG`, then confirm in the
   live plot that a target physically in front appears at 0 degrees.
4. Start at `NAV_DRIVE_SPEED=90`. Measure stopping distance on the worst floor
   and set `LIDAR_SAFETY_DISTANCE_MM` beyond the footprint plus measured braking
   distance and margin.
5. Confirm the E-stop button and Space key latch the base. Confirm every motion
   command is rejected until **Re-arm** succeeds.
6. Disconnect LiDAR while moving on blocks. The base must stop within
   `NAV_MAX_SCAN_AGE_S` plus serial/control latency, enter
   `WAITING_FOR_SENSORS`, resume only after three healthy scans, and enter
   `FAULT` if health does not recover before `NAV_SENSOR_START_TIMEOUT_S`.
7. Disconnect the ESP32 telemetry TX line. The base must stop after
   `NAV_MAX_WHEEL_TELEMETRY_AGE_S` and use the same bounded sensor-recovery
   sequence.
8. Place obstacles at front, rear, both sides, and each corner. Rotation must be
   rejected whenever the swept footprint is blocked.
9. Run blocked-corridor and feature-poor-room trials. Cubey must enter `FAULT`
   rather than repeatedly rotating.
10. Inspect `data/logs/cubey.log` after every trial.

Do not hide chassis reflections by raising `LIDAR_MIN_VALID_DISTANCE_MM` to a
large value. Use the smallest reliable sensor range and correct the physical
mount so the scan plane clears the shell.

## Production navigation architecture

The final deployment target is ROS 2 Jazzy on Ubuntu 24.04 ARM64:

- Slamtec `sllidar_ros2` C1 driver publishing timestamped `LaserScan` data.
- A URDF defining `base_footprint`, `base_link`, `laser`, IMU, and wheel frames.
- Four wheel encoders and a 6-axis IMU.
- Mecanum wheel odometry at 50-100 Hz, fused with the IMU by
  `robot_localization`, publishing `odom -> base_link` and measured velocity.
- `slam_toolbox` publishing `map -> odom`, pose-graph mapping, loop closure, and
  serialized maps.
- Nav2 rolling local and global costmaps using Cubey's measured polygon.
- A holonomic controller, velocity smoother, and Collision Monitor as the last
  publisher before the base driver.
- Frontier exploration issuing bounded `NavigateToPose` goals. The exploration
  selector must never publish motor commands directly.
- `rosbag2` recording `/scan`, `/odom`, `/tf`, `/tf_static`, command velocity,
  collision-monitor state, diagnostics, and navigation results.

The required transform chain is `map -> odom -> base_link -> laser`. Encoders
and an IMU are the remaining hardware requirement: open-loop PWM is not a
production odometry source for a mecanum base because lateral slip and rotation
cannot be inferred reliably from commands.

## Incident response

If Cubey contacts anything or rotates unexpectedly:

1. Press E-stop; do not immediately re-arm.
2. Copy `data/logs/cubey.log` and preserve any ROS bag from the run.
3. Record the floor type, battery voltage, starting pose, obstacle position,
   LiDAR scan rate, and navigation fault reason from `/api/status`.
4. Reproduce on blocks or in simulation before another floor test.
