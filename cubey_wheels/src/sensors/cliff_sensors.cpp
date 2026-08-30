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

  if (!frontSensorReady) {
    serialPrintln("WARNING: Front cliff sensor failed");
  } else {
    serialPrintln("Front cliff sensor connected");
  }

  // Start back sensor and assign address 0x31.
  digitalWrite(BACK_XSHUT, HIGH);
  delay(100);

  backSensorReady = backSensor.begin(0x31, false, &Wire);

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
  VL53L0X_RangingMeasurementData_t measurement;

  sensor.rangingTest(&measurement, false);

  distance = measurement.RangeMilliMeter;

  // RangeStatus 4 means no usable target / out of range.
  return measurement.RangeStatus != 4;
}

// ============================================================
// CLIFF SAFETY UPDATE
// ============================================================
void updateCliffSafety() {
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

    if (dangerous) {
      frontCliffCount++;
    } else {
      frontCliffCount = 0;
    }

    frontCliff =
      frontCliffCount >= CLIFF_CONFIRM_READINGS;
  } else {
    frontCliff = true;
  }

  // ---------------- Back ----------------
  if (backSensorReady) {
    bool valid = readFloorSensor(backSensor, backDistance);

    bool dangerous =
      !valid ||
      backDistance > CLIFF_DISTANCE_MM;

    if (dangerous) {
      backCliffCount++;
    } else {
      backCliffCount = 0;
    }

    backCliff =
      backCliffCount >= CLIFF_CONFIRM_READINGS;
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
    performSafetyEscape(false);
    return;
  }

  // Newly detected back cliff while moving toward the back.
  if (
    backCliff &&
    !previousBackCliff &&
    movesTowardBack(currentMotion)
  ) {
    performSafetyEscape(true);
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
