# Cubey: IMU heading, autonomous room mapping, and return to start

Status: implemented on `feat/imu-mapping-return-home`. See `ros2/IMU_NAVIGATION.md` for bring-up and validation. Stationary robot verification is in progress; physical mapping/return acceptance remains an operator test.

## Intended behavior

Click **Start Mapping** in the web interface. Cubey checks its sensors, records its starting position and heading, maps the accessible room, returns to that position and heading, stops, and saves the final map. The interface shows the live heading and mission phase throughout.

For this task, home means the pose where this mapping session began. It does not mean locating or electrically docking with a charging station. Initial acceptance testing uses an enclosed room; frontier exploration can otherwise continue through open doors into connected spaces.

## What the existing code shows

The supplied workspace contains the matching BNO08x test implementation in `cubey_wheels/`; a separate directory named `cubeyWheels` was not found in the inspected locations.

| Area | Finding | Consequence |
| --- | --- | --- |
| `cubey_wheels/src/sensors/imu.cpp` | Requests the game rotation vector at 50 Hz. Marks `imuReady` true even if report subscription fails, and does not expire readiness when samples stop. | `IMU:ok=1` alone is insufficient evidence of a healthy live stream. |
| `cubey_wheels/src/comm/serial_comm.cpp` | Full quaternion is returned by the manual `IMU` command. Continuous telemetry contains rounded Euler angles at approximately 4 Hz. | The existing output is useful for diagnosis but is not yet a navigation-quality data feed. |
| `ros2/nodes/cmd_vel_serial_bridge.py` | Copies every received serial line into one latest-line file; does not publish ROS IMU messages. | ACKs, diagnostics, and future high-rate IMU packets can overwrite general telemetry. |
| `ros2/nodes/cubey_odometry_node.py` | Searches for yaw using scans and command estimates, suppresses updates when commanded motion is zero, and publishes commanded velocities as odometry twist. | Physical heading is not measured by the IMU; commands can disagree with actual motion, including teleoperation and stalls. |
| `ros2/nodes/cubey_frontier_explorer_node.py` | Sets home to `(0, 0, 0)` after reset; does not validate the odometry-reset response; pauses SLAM before returning. | The start reference is assumed and global scan corrections stop during the journey home. These are plausible contributors to the reported return failure, not a confirmed diagnosis from a recorded run. |
| Same explorer | Accepts home within 35 cm without checking home yaw. | A completed mission does not enforce the requested starting pose. |
| Explorer and web WebSocket router | Exports pose with map callbacks; web reuses that file without checking its age. | Heading can lag behind motion or remain stale despite the WebSocket running at 10 Hz. |

## Architecture decision

Use the IMU as the local heading reference, measured LiDAR displacement for translation, and SLAM Toolbox for the room map and global corrections. Add the standard ROS `robot_localization` EKF to produce the odometry used by Nav2.

```text
BNO08x -> ESP32 -> existing UART bridge -> /imu/data --------+
                                                          +-> EKF -> /odom -> Nav2
RPLIDAR -> /scan -> IMU-assisted translation measurement ---+
              |
              +-> SLAM Toolbox, using fused odometry -> /map and map->odom

TF: map -> odom -> base_link -> laser
                           -> imu_link
```

Only the EKF publishes `odom -> base_link`; only SLAM publishes `map -> odom`. Raw translation measurements publish no competing transform. Do not feed SLAM's corrected map pose back into this local filter.

The IMU does not replace positional sensing. The current game rotation vector uses gyro and accelerometer fusion without magnetic heading correction, so yaw can drift over time. Retain LiDAR corrections and loop closure; do not integrate acceleration twice to invent room position. [BNO08x datasheet](https://www.ceva-ip.com/wp-content/uploads/2019/10/BNO080_085-Datasheet.pdf)

## Implementation sequence

### 1. Validate mounting and make the firmware stream usable

- Verify the exact board axes and its mounting with Cubey level, then check physical left/right 90-degree turns. ROS body axes are forward X, left Y, up Z, with positive yaw for counterclockwise turns.
- The VCC/GND-front and PS0/PS1-rear description helps identify mounting, but does not uniquely establish the board axes or which face points upward. Configure a measured full mounting transform instead of guessing a 90/180-degree yaw offset.
- Investigate the reported roll of approximately -14.65 degrees while Cubey is on a level surface. Separate mounting tilt from robot tilt; do not simply force the incoming quaternion's X/Y components to zero.
- Stream full quaternions at a target 50 Hz over the dedicated Pi UART, with sequence number, sample time, sample validity/calibration status, and sensor reset generation. Preserve the manual `IMU` command for diagnosis.
- Add measured gyro angular velocity if supported by the enabled reports; identify its timestamp separately when reports arrive asynchronously.
- Assert healthy status only after valid fresh reports arrive. On sensor reset, failed subscriptions, or stale samples, invalidate the stream immediately and restore reports explicitly.
- Keep acquisition and output bounded so IMU work does not delay wheel commands, cliff sensing, or the motor watchdog. Check the complete traffic budget at 115200 baud and suppress high-rate command echo where needed.

Files: `cubey_wheels/src/sensors/imu.cpp`, `imu.h`, `src/config/config.h`, `src/comm/serial_comm.cpp`, and the main sketch loop as needed.

### 2. Publish a correctly framed and timestamped ROS IMU

- Extend the existing UART owner rather than opening the ESP32 serial port from a second node.
- Parse IMU and ordinary telemetry into separate paths. Preserve the most recent complete general telemetry snapshot; diagnostics must not replace it.
- Publish `sensor_msgs/Imu` on `/imu/data` in `imu_link`, with finite normalized quaternion values and measured covariances. Mark unavailable angular velocity or acceleration with the message's unavailable-data convention; never report fictitious zero measurements. [ROS Jazzy Imu message](https://raw.githubusercontent.com/ros2/common_interfaces/jazzy/sensor_msgs/msg/Imu.msg)
- Map sensor sample times into ROS time; handle duplicates, partial lines, out-of-order data, counter wrap, reconnects, and resets. Report freshness from acquisition time, not just receipt of another telemetry line.
- Expose IMU health and sample age to the mission supervisor and web interface.

Files: `ros2/nodes/cmd_vel_serial_bridge.py`, `src/services/wheels_service.py`, and launch/config additions for `base_link -> imu_link`.

### 3. Replace guessed heading with fused measured odometry

- Refactor the current scan odometry into a translation measurement source. Remove its free yaw grid search and use timestamp-aligned IMU rotation when aligning scans.
- Account for the LiDAR's configured position behind `base_link`; rotating an offset sensor must not look like base translation.
- Estimate translation and velocity from scan observations with residual/overlap checks and conservative covariance. Reject ambiguous matches. Do not require a nonzero `/cmd_vel` before recognizing physical motion.
- Treat motor commands only as optional search predictions. Do not publish them as measured velocity, and do not advance pose merely because the motors were commanded.
- Add a 2D EKF configuration, initially targeting 30-50 Hz. Fuse selected LiDAR translation measurements and IMU heading; avoid feeding IMU-derived yaw back through another odometry input as independent evidence. Account conservatively for the correlation introduced by IMU-assisted scan alignment.
- Apply mounting correction before extracting planar heading and establish the session's heading reference while stationary. Handle yaw wrap and quaternion sign equivalence. Reset filter state, reference, and sensor queues together for a fresh session.
- Audit the custom C1 driver's scan timestamps, angular ordering, and `time_increment` before synchronizing scans with IMU samples. Its current end-of-sweep timestamp and rebinned ordering need explicit treatment during turns.
- Add the Jazzy `robot_localization` dependency and update launch ownership of `/odom` and TF. Resolve and validate the dependency in the existing Pi/RoboStack environment.

Correct frame conventions and sensor covariance are requirements of the chosen filter. [robot_localization sensor preparation](https://github.com/cra-ros-pkg/robot_localization/blob/rolling-devel/doc/preparing_sensor_data.rst)

Files: `ros2/nodes/cubey_odometry_node.py`, `ros2/nodes/rplidar_c1_node.py`, `ros2/config/ekf_params.yaml` (new), `ros2/launch/cubey_bringup.launch.py`, `ros2/pixi.toml`, and relevant startup scripts.

### 4. Make mapping start an acknowledged mission

- Use explicit phases: `PREPARING -> EXPLORING -> RETURNING_HOME -> FINALIZING -> COMPLETED`, with separate stopped, blocked, and failed outcomes.
- On Start Mapping, cancel prior navigation, enforce zero drive, and wait for fresh IMU/scans, valid transforms, and ready Nav2/SLAM components. A fresh explorer heartbeat alone does not prove readiness.
- Reset odometry/filter and SLAM in a coordinated sequence, validating every response and discarding samples/transforms from the preceding session.
- Capture home X/Y/yaw from the first valid post-reset map-frame base pose while stationary, before issuing an exploration goal. Preserve that map reference across occupancy-grid growth and loop closure; never replace home with the grid's changing origin.
- Give commands and asynchronous callbacks a mission generation so delayed start/reset/save results cannot restart a stopped mission or affect a later one.
- Make HTTP and WebSocket acknowledgements reflect preparation and acceptance. If startup times out, cancel the pending mission so it cannot begin after the interface reports failure.

Files: explorer, `src/services/navigation/cubey_nav_service.py`, `src/web/routers/api_navigation.py`, and `src/web/routers/ws.py`.

### 5. Complete exploration and return with localization still running

- Retain frontier exploration and planner reachability checks. Require fresh maps, repeated exhaustion of reachable frontiers, and completion of pending navigation/survey actions before ending exploration.
- Bound unsuccessful exploration/recovery cycles. Distinguish an exhausted accessible room from widespread planning or localization failure.
- Save a checkpoint when exploration finishes, then navigate to the saved home pose while SLAM continues processing scans and correcting position. The current pause service stops new scan processing; it is not a switch to localization mode. [SLAM Toolbox Jazzy documentation](https://raw.githubusercontent.com/SteveMacenski/slam_toolbox/jazzy/README.md)
- Use the existing Nav2 controller for the return, tuning turns, progress checking, footprint, and approach speed against actual movement. Keep final-heading turns from being misclassified as translational stalls.
- Try the exact home pose first. Nearby reachable points may be intermediate approach goals, followed by a final approach and orientation step; proximity alone must not count as completion.
- Replan around obstacles with bounded retries. If home is inaccessible or sensor/localization health is lost, stop and report the specific failure while retaining the checkpoint.
- Declare success only with a fresh localized pose inside both configurable position and yaw tolerances, settled motion, and confirmed final map save. Explicitly cancel remaining actions and command zero drive on completion/failure.
- Save the final occupancy map and serialized SLAM graph after the return and loop-closure settling. Save the session's home pose and result alongside them. Stop map updates after Cubey is home and stopped, if desired.

Files: explorer, `ros2/config/nav2_params.yaml`, `ros2/config/slam_toolbox_params.yaml`, and navigation-service outcome handling.

### 6. Show the same live pose used by Nav2

- Export map-frame robot pose independently of the slower occupancy-grid export, targeting 10-20 Hz.
- Make the web map arrow, trajectory, and home marker use the same ROS coordinate convention and pose source as navigation. Check canvas angle signs explicitly.
- Show preparation, mapping, returning home, and final result distinctly; keep return failure visible rather than collapsing every outcome to `IDLE`.
- Display IMU health and stale localization explicitly. Do not substitute legacy map/pose estimates into an active ROS mission.
- Verify that Start Mapping, pause, reset, stop, and repeated clicks behave consistently through both HTTP and WebSocket paths. The existing primary Start Mapping button already requests autonomous mode.

Files: explorer exports, WebSocket router, `src/web/static/app.js`, `src/web/static/map.html`, and shared UI code as needed.

## Validation and definition of done

Automated coverage should exercise actual outcomes: quaternion/frame transforms and wraparound, sample staleness/reset handling, motion with zero command, commanded-but-stalled motion, scan timing and sensor offset during turns, consistent filter resets, cancellation races, start-pose capture, continued localization on return, arrival heading, save failure, and fresh web pose delivery. Replace tests that currently enforce pausing SLAM before returning.

Run appropriate firmware compilation, Python behavior tests, and ROS integration/bag replay in the Pi's Jazzy environment. Record IMU, scans, odometry, TF, commands, and mission status for comparison with physical results.

Initial physical acceptance targets, to be measured rather than claimed in advance:

1. Fresh IMU delivery near 50 Hz under motor-command load, with no sustained UART backlog or watchdog interference.
2. Left/right 90-degree and full-circle turns agree with independent physical markings; target heading error no greater than 5 degrees and web response within 200 ms during healthy operation.
3. Forward, reverse, sideways, stalled-wheel, and stationary observations produce sensible measured odometry, including after a full rotation.
4. Three consecutive enclosed-room missions started with one web click map the accessible space, return within 10 cm and 5 degrees of a physically marked starting pose, stop, and save a usable final map. Measure position and heading externally as well as in ROS.
5. A second mission from a different location and heading uses its own home pose; prior-session data cannot leak into it.
6. IMU disconnect/reset, LiDAR loss, stale TF, blocked home, map-save failure, and stop during preparation/return all produce the expected bounded stop or failure outcome, with no false success or later unexpected motion.

The remaining hardware-dependent facts are the exact sensor-to-body transform, reliable stream timing, motor response, and achievable localization accuracy. This plan addresses the observed code defects; robot tests determine whether the complete experience meets the acceptance targets.
