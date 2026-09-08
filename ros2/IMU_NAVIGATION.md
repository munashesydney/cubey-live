# IMU navigation bring-up

The ESP32 sketch and Pi branch must be updated together. The bridge requires
timestamped `IMU:` packets; the old manual quaternion response is diagnostic only.

The local heading/translation node publishes `/imu/data` and `/odom/lidar`.
`robot_localization` owns `/odom` and `odom -> base_link`; SLAM owns `map -> odom`.
No motor command is fused as a sensor measurement. Missing IMU, unobservable scan
translation, or stale localization inhibits navigation and stops an active mission.

The IMU sketch wraps Adafruit's I2C HAL to supply its missing host timestamp.
Without this adapter, timestamps observed on Cubey stayed near 4294965xxx and
regressed, although the orientation quaternion looked valid. Do not work around
this by treating old packets as fresh readings in the Pi bridge.

## Stationary checks

Run from the `ros2` directory in the Pixi environment after starting services:

```sh
pixi run ros2 topic hz /imu/raw
pixi run ros2 topic hz /imu/data
pixi run ros2 topic echo /cubey/imu_status --once
pixi run ros2 topic echo /cubey/odometry_status --once
pixi run ros2 topic echo /cubey/exploration_status --once
pixi run ros2 run tf2_ros tf2_echo map base_link
```

Expect fresh IMU samples near 50 Hz, increasing sample times, and stationary
orientation. `cal=3` is the sensor's reported accuracy status, not proof of
correct chassis mounting. The supervisor should remain `IDLE` until a user starts
mapping. A stationary test cannot verify turn direction or physical return accuracy.

Mounting angles are launch arguments `imu_mount_roll`, `imu_mount_pitch`, and
`imu_mount_yaw`, in radians, describing base_link to imu_link. They default to
zero and must be checked against the actual board. The code applies the full
mounting quaternion before extracting heading and establishes a new relative
heading reference on each mapping reset. Do not derive a full mounting transform
from connector labels alone or assume the reported roll is a calibration error.

The C1 driver compensates rotational scan distortion using the IMU and the known
35 mm rearward LiDAR offset. Translational deskew is not implemented; mapping
speed remains 0.12 m/s. Sensor polling and complete-sweep timing must be validated
on the Pi under load.

## Mapping behavior

The main web **Start Mapping** button starts autonomous preparation directly.
It holds motion, resets measurements/EKF/SLAM, waits for fresh stationary pose,
then captures home before exploring. During return, SLAM keeps correcting pose.
Nearby approach points are intermediate goals. Completion requires the original
position within 10 cm, heading within 5 degrees, settled motion, and final map save.

Maps are saved in `data/maps`: a checkpoint before returning, and a final map,
serialized graph, and `.session.json` after returning. A blocked return produces
`COMPLETED_AWAY_FROM_DOCK`; sensor/save failures produce `ERROR` with a reason.
The web exposes these outcomes instead of replacing them with an idle label.

## Checks performed locally

- ESP32-S3 firmware compilation with Arduino ESP32 core 3.3.11 and USB CDC enabled.
- Behavioral tests in `test_imu_navigation.py`, `test_nav2_integration.py`,
  `test_wheels_service.py`, and `test_web_server.py`.
- Python compilation and JavaScript syntax validation.

The legacy `test_raycasting_marks_free_and_occupied` fails identically using the
unchanged original MappingService; it expects a north-facing legacy ray endpoint.
The autonomous ROS path no longer runs that separate legacy mapper.

Physical turn, driving, and room-mapping acceptance checks are reserved for the
operator. Targets are three consecutive missions returning within 10 cm and
5 degrees of independently marked home, including a subsequent session started
at a different pose. No stationary test establishes those results.

## Pi stationary verification — 2026-09-07

After the corrected ESP firmware upload, the live IMU produced about 50 Hz with
no non-increasing timestamps. A settled 30-second sample measured 0.0088 degrees
of heading variation, raw sample age at most 39 ms, processed IMU age at most
114 ms, LiDAR at 10 Hz, and filtered odometry near 36 Hz. The stationary reset
service returned to IDLE with fresh pose and healthy measurement status.

Filtered position wandered up to 5.7 cm from the first sample while stationary;
this is a remaining estimation limitation, not evidence of physical movement.
One sensor epoch change was observed without an ESP boot change. The cause is
unresolved; the odometry restart fault latched as intended. A ROS service restart
recovered the current sensor stream. Another 30-second check remained healthy,
with no timestamp regressions and 0.017 degrees of heading variation.

Nav2 activation had timed out while the old firmware supplied no valid odometry.
Restarting ROS with corrected firmware activated controller_server,
planner_server, bt_navigator, and behavior_server successfully. Both systemd
services were active, and the web endpoint responded with its authentication
challenge. No exploration, navigation goal, or nonzero motor command was sent.
Motor telemetry confirmed STOPPED. Battery telemetry was approximately 13%.
Mounting/turn direction, reset reliability over longer runs, and physical
mapping/return accuracy still require operator testing.

### Follow-up: clock correction and failed web reset

The next Pi boot logged initial NTP synchronization at 17:19:08 after starting
services with a clock several minutes behind. This invalidated the original
wall-clock-to-IMU offset: raw serial samples remained healthy at 50 Hz while ROS
rejected them as backlog. The filter continued predicting position without new
measurements, producing a spurious position over 20 metres from the origin.

The bridge now compares wall and monotonic receipt times, adjusts the timestamp
offset on a clock step, and exposes a new clock generation. This preserves real
backlog rejection and requires a localization reset after a clock discontinuity.
The web no longer updates its avatar from unhealthy localization. Map reset
acknowledgments now match a unique request ID, preserve the web map on failure,
and report the supervisor's failure reason. Successful reset recentres the view.

After deployment, the actual authenticated POST /api/mapping/reset returned
HTTP 200 in 2.48 seconds. Pose was within 3 mm of the origin, IMU healthy, state
IDLE, and motors STOPPED. Local verification passed 88 targeted tests, including
forward/backward host-clock steps, retained backlog rejection, and reset request
matching; JavaScript syntax validation also passed. No mapping was started.

### First operator-driven mapping mission: checkpoint transition

Mission 8db74039-b9d6-45ab-8b04-7235aae8114a reached six frontier goals and
completed exploration. It entered return preparation at 17:31:32, requesting
the checkpoint save. The generic localization watchdog aborted at 17:31:33;
the save completed successfully at 17:31:34. No home navigation goal was sent.
The logs indicate a transient localization delay during saving; the previous
generic error did not record which freshness threshold tripped.

Checkpoint saving now holds motion throughout its bounded 20-second operation.
After success, the supervisor waits up to 10 seconds for healthy sensors, fresh
TF/odometry and map, requiring 0.75 seconds of continuous readiness before
planning home. Moving-state freshness checks remain enforced. Future freshness
failures include TF, odometry and map ages. Tests cover delayed save localization,
interrupted recovery, timeout, and no premature return dispatch.

The original checkpoint PGM/YAML, serialized pose graph/data, and diagnostic
home/pose metadata are preserved on the Pi under
data/maps/cubey_floorplan_20260907_173132_checkpoint.*.

### Recovery from brief measurement delays

During navigation, a temporary measurement/TF/map delay now cancels the active
goal and enters RECOVERING_LOCALIZATION with motion disabled. It retains the
mission and destination, allowing up to 10 seconds to recover. One continuous
second of healthy measurements, fresh pose/map, and available Nav2 servers is
required before sending a new navigation goal (and therefore a new path).
The recovered pose must remain within 25 cm and 30 degrees of the last trusted
pose. Interrupted exploration without an active destination selects a frontier
again; interrupted home planning replans home. Interrupted backup is cancelled,
and the parent exploration/return phase is restored rather than replaying backup.

An explicit IMU/clock fault, changed localization reference, excessive pose
change, recovery timeout, or fourth interruption within 60 seconds ends the
mission stopped. Stop/Reset invalidates recovery through the mission state.
The web shows the paused recovery phase. These paths are covered by local tests;
no automatic-resume driving test has been performed on the robot.
