# Sequential cliff acquisition

Upload `cubey_wheels.ino` and its accompanying `src` directory using the same
ESP32-S3 board and USB settings as the previous firmware. A Pi git pull cannot
replace the firmware running on the ESP.

This replaces the IMU branch's continuous VL53L0X ranging with alternating
single-shot measurements. The loop polls completion instead of waiting for an
exposure. Telemetry consumes cached samples and cannot start or consume a range.
Pins, I2C addresses and the 140 mm cliff threshold are unchanged.

An exposure has a 120 ms deadline. A timed-out sensor is shut down before the
other sensor starts. Driver errors or three consecutive invalid readings cause
sensor reinitialization while the motors are stopped. Initialization failures
retry after two seconds. Sensor calibration can briefly delay IMU polling during
this stopped recovery; navigation must remain subject to its sensor-health gate.
Missing or older-than-150-ms floor samples remain unsafe. Confirmation counts
advance on new samples, not repeated reads of the cache. An invalid sensor result
stops motion toward that sensor instead of triggering a recoil maneuver.

After upload, `STATUS` and Pi telemetry must include:

    cliff_acquisition=sequential_v2

Each sensor also reports `range_valid`, `range_status`, `sensor_error`, and
`sample_age_ms`. Range status 0 and driver error 0 are required for a valid range.
Raw 8191/65535 values are retained as diagnostic evidence, never converted to a
fake safe distance. Recovery messages are captured by the Pi UART bridge journal.

Stationary validation: place Cubey on the normal floor, confirm both validity
flags are 1, ages remain below 150 ms and both cliff flags are 0. Monitor for at
least a minute together with IMU health. Only the user performs movement tests.
Physical validation remains pending until this firmware is uploaded.
