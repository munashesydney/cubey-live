#include "cliff_sensors.h"
#include "../config/config.h"
#include "../motion/motors.h"
#include "../comm/serial_comm.h"

// State definitions
Adafruit_VL53L0X frontSensor;
Adafruit_VL53L0X backSensor;

bool frontSensorReady = false;
bool backSensorReady = false;

bool frontCliff = false;
bool backCliff = false;

int frontCliffCount = 0;
int backCliffCount = 0;

unsigned long lastSensorCheck = 0;
bool safetyMovementRunning = false;

struct FloorSample {
  uint16_t distance = 0xffff;
  bool valid = false;
  unsigned long timestamp = 0;
  uint8_t rangeStatus = 255;
  int error = 0;
  uint32_t sequence = 0;
  uint32_t checkedSequence = 0;
  uint8_t failures = 0;
  bool heldReset = false;
  unsigned long retryAt = 0;
};
static FloorSample frontSample;
static FloorSample backSample;
static int activeSensor = -1;
static int nextSensor = 0;
static unsigned long exposureStarted = 0;
static unsigned long lastPoll = 0;
static const unsigned long SAMPLE_MAX_AGE_MS = 150;
static const unsigned long EXPOSURE_TIMEOUT_MS = 120;

// Only this scheduler touches ranging hardware. Telemetry reads cached data.
// One laser at a time restores main's sequential acquisition without blocking
// the ESP loop while an exposure is in progress.
static void pollFloorSensors() {
  const unsigned long now = millis();
  if (now-lastPoll < 2) return;
  lastPoll = now;
  if (activeSensor >= 0) {
    const bool front = activeSensor == 0;
    auto &sensor = front ? frontSensor : backSensor;
    auto &sample = front ? frontSample : backSample;
    const bool complete = sensor.isRangeComplete();
    const bool timedOut = now-exposureStarted >= EXPOSURE_TIMEOUT_MS;
    if (!complete && !timedOut) return;
    sample.error = sensor.Status;
    if (complete && sample.error == 0) {
      sample.distance = sensor.readRangeResult();
      sample.error = sensor.Status;
      sample.rangeStatus = sensor.readRangeStatus();
    } else {
      sample.distance = 0xffff;
      sample.rangeStatus = 255;
      if (timedOut && sample.error == 0) sample.error = -7;
    }
    sample.valid = sample.error == 0 && sample.rangeStatus == 0 && sample.distance < 8190;
    sample.timestamp = now;
    ++sample.sequence;
    sample.failures = sample.valid ? 0 : (sample.failures < 255 ? sample.failures+1 : 255);
    // End a timed-out exposure before starting the other laser. Reinitialize
    // only while stopped: the driver's calibration itself is blocking.
    if (timedOut || sample.error != 0 || (sample.failures >= 3 && !motorsRunning)) {
      digitalWrite(front ? FRONT_XSHUT : BACK_XSHUT, LOW);
      sample.heldReset = true;
      sample.retryAt = now+1000;
      sample.valid = false;
      serialPrintln(String("CLIFF_SENSOR_RECOVERY:")+(front ? "front" : "back")+
                    ",status="+String(sample.rangeStatus)+",error="+String(sample.error));
    }
    nextSensor = front ? 1 : 0;
    activeSensor = -1;
  }
  for (int attempt = 0; attempt < 2; ++attempt) {
    const bool front = nextSensor == 0;
    auto &sensor = front ? frontSensor : backSensor;
    auto &sample = front ? frontSample : backSample;
    bool &ready = front ? frontSensorReady : backSensorReady;
    if (sample.heldReset && !motorsRunning && (long)(now-sample.retryAt) >= 0) {
      digitalWrite(front ? FRONT_XSHUT : BACK_XSHUT, HIGH);
      delay(10); // Boot only; exposures never use delay/waitRangeComplete.
      ready = sensor.begin(front ? 0x30 : 0x31, false, &Wire);
      sample.heldReset = !ready;
      if (!ready) digitalWrite(front ? FRONT_XSHUT : BACK_XSHUT, LOW);
      sample.retryAt = millis()+2000;
      sample.failures = 0;
      serialPrintln(String("CLIFF_SENSOR_REINITIALIZED:")+(front ? "front" : "back")+",ok="+String(ready));
    }
    if (ready && !sample.heldReset) {
      if (sensor.startRange()) {
        activeSensor = front ? 0 : 1;
        exposureStarted = millis();
        return;
      }
      sample.valid = false;
      sample.error = sensor.Status;
      sample.rangeStatus = 255;
      sample.distance = 0xffff;
      ++sample.sequence;
      digitalWrite(front ? FRONT_XSHUT : BACK_XSHUT, LOW);
      sample.heldReset = true;
      sample.retryAt = now+1000;
    }
    nextSensor = front ? 1 : 0;
  }
}

// ============================================================
// CLIFF SENSOR SETUP
// ============================================================
void setupCliffSensors() {
  serialPrintln("Starting cliff sensors...");

  Wire.begin(SENSOR_SDA, SENSOR_SCL);

  pinMode(FRONT_XSHUT, OUTPUT);
  pinMode(BACK_XSHUT, OUTPUT);

  // Shut down both sensors because they initially share address 0x29.
  digitalWrite(FRONT_XSHUT, LOW);
  digitalWrite(BACK_XSHUT, LOW);
  delay(100);

  // Start front sensor and assign address 0x30.
  digitalWrite(FRONT_XSHUT, HIGH);
  delay(100);

  frontSensorReady = frontSensor.begin(0x30, false, &Wire);
  frontSample.heldReset = !frontSensorReady;
  if (!frontSensorReady) digitalWrite(FRONT_XSHUT, LOW);

  if (!frontSensorReady) {
    serialPrintln("WARNING: Front cliff sensor failed");
  } else {
    serialPrintln("Front cliff sensor connected");
  }

  // Start back sensor and assign address 0x31.
  digitalWrite(BACK_XSHUT, HIGH);
  delay(100);

  backSensorReady = backSensor.begin(0x31, false, &Wire);
  backSample.heldReset = !backSensorReady;
  if (!backSensorReady) digitalWrite(BACK_XSHUT, LOW);

  if (!backSensorReady) {
    serialPrintln("WARNING: Back cliff sensor failed");
  } else {
    serialPrintln("Back cliff sensor connected");
  }

  // Fail-safe behavior:
  // If a sensor failed, block movement toward that direction.
  frontCliff = !frontSensorReady;
  backCliff = !backSensorReady;
}

// ============================================================
// READ ONE FLOOR SENSOR
// ============================================================
bool readFloorSensor(
  Adafruit_VL53L0X &sensor,
  uint16_t &distance
) {
  FloorSample &sample = (&sensor == &frontSensor) ? frontSample : backSample;
  distance = sample.distance;
  return sample.valid && !sample.heldReset && millis() - sample.timestamp <= SAMPLE_MAX_AGE_MS;
}

String floorSensorDiagnostics() {
  String result = ",cliff_acquisition=sequential_v2";
  for (int i = 0; i < 2; ++i) {
    auto &sample = i == 0 ? frontSample : backSample;
    auto &sensor = i == 0 ? frontSensor : backSensor;
    const String prefix = i == 0 ? ",front_" : ",back_";
    uint16_t distance;
    result += prefix+"range_valid="+String(readFloorSensor(sensor, distance) ? 1 : 0);
    result += prefix+"range_status="+String(sample.rangeStatus);
    result += prefix+"sensor_error="+String(sample.error);
    result += prefix+"sample_age_ms="+String(sample.sequence ? millis()-sample.timestamp : 0xffffffffUL);
  }
  return result;
}

// ============================================================
// CLIFF SAFETY UPDATE
// ============================================================
void updateCliffSafety() {
  pollFloorSensors();
  if (millis() - lastSensorCheck < SENSOR_INTERVAL_MS) {
    return;
  }

  lastSensorCheck = millis();

  bool previousFrontCliff = frontCliff;
  bool previousBackCliff = backCliff;

  uint16_t frontDistance = 0;
  uint16_t backDistance = 0;

  // ---------------- Front ----------------
  if (frontSensorReady) {
    bool valid = readFloorSensor(frontSensor, frontDistance);

    bool dangerous =
      !valid ||
      frontDistance > CLIFF_DISTANCE_MM;

    if (frontSample.sequence != frontSample.checkedSequence && dangerous) {
      frontCliffCount++;
    } else if (!dangerous) {
      frontCliffCount = 0;
    }
    frontSample.checkedSequence = frontSample.sequence;

    frontCliff =
      frontCliffCount >= CLIFF_CONFIRM_READINGS || frontSample.heldReset ||
      !frontSample.sequence || millis()-frontSample.timestamp > SAMPLE_MAX_AGE_MS;
  } else {
    frontCliff = true;
  }

  // ---------------- Back ----------------
  if (backSensorReady) {
    bool valid = readFloorSensor(backSensor, backDistance);

    bool dangerous =
      !valid ||
      backDistance > CLIFF_DISTANCE_MM;

    if (backSample.sequence != backSample.checkedSequence && dangerous) {
      backCliffCount++;
    } else if (!dangerous) {
      backCliffCount = 0;
    }
    backSample.checkedSequence = backSample.sequence;

    backCliff =
      backCliffCount >= CLIFF_CONFIRM_READINGS || backSample.heldReset ||
      !backSample.sequence || millis()-backSample.timestamp > SAMPLE_MAX_AGE_MS;
  } else {
    backCliff = true;
  }

  // Print ONLY when an edge or cliff is newly detected (unsafe).
  if (
    (frontCliff && !previousFrontCliff) ||
    (backCliff && !previousBackCliff)
  ) {
    serialPrint("[CLIFF DETECTED] Front: ");
    serialPrint(String(frontDistance));
    serialPrint(" mm | Back: ");
    serialPrint(String(backDistance));
    serialPrint(" mm | Front cliff: ");
    serialPrint(frontCliff ? "YES" : "NO");
    serialPrint(" | Back cliff: ");
    serialPrintln(backCliff ? "YES" : "NO");
  }

  // Newly detected front cliff while moving toward the front.
  if (
    frontCliff &&
    !previousFrontCliff &&
    movesTowardFront(currentMotion)
  ) {
    if (readFloorSensor(frontSensor, frontDistance)) performSafetyEscape(false);
    else stopAll(); // Sensor failure is not evidence of a measured drop.
    return;
  }

  // Newly detected back cliff while moving toward the back.
  if (
    backCliff &&
    !previousBackCliff &&
    movesTowardBack(currentMotion)
  ) {
    if (readFloorSensor(backSensor, backDistance)) performSafetyEscape(true);
    else stopAll();
    return;
  }

  // Stop sideways/rotation if either edge sensor sees danger.
  if (
    (frontCliff || backCliff) &&
    (
      currentMotion == STRAFE_LEFT ||
      currentMotion == STRAFE_RIGHT ||
      currentMotion == ROTATE_LEFT ||
      currentMotion == ROTATE_RIGHT ||
      (
        currentMotion == VELOCITY_CONTROL &&
        (
          twistLeft != 0 ||
          twistCounterClockwise != 0 ||
          (frontCliff && twistForward >= 0) ||
          (backCliff && twistForward <= 0)
        )
      )
    )
  ) {
    serialPrintln("SAFETY: Sideways/rotation stopped");
    stopAll();
  }
}

// ============================================================
// SMALL AUTOMATIC SAFETY RECOIL
// ============================================================
void performSafetyEscape(bool moveForward) {
  if (safetyMovementRunning) {
    return;
  }

  safetyMovementRunning = true;

  serialPrintln("!!! CLIFF DETECTED !!!");

  stopAll();
  delay(60);

  int previousSpeed = motorSpeed;
  motorSpeed = SAFETY_ESCAPE_SPEED;

  if (moveForward) {
    serialPrintln("Safety recoil: moving forward");
    // All wheels forward.
    driveWheels(1, 1, 1, 1);
  } else {
    serialPrintln("Safety recoil: moving backward");
    // All wheels backward.
    driveWheels(-1, -1, -1, -1);
  }

  delay(SAFETY_ESCAPE_TIME_MS);

  driveWheels(0, 0, 0, 0);

  motorSpeed = previousSpeed;
  currentMotion = STOPPED;
  motorsRunning = false;
  safetyMovementRunning = false;
  serialPrintln("Safety recoil complete");
}
