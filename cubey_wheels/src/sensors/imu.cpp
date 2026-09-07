#include "imu.h"
#include "../config/config.h"
#include "../comm/serial_comm.h"
#include <Wire.h>
#include <Adafruit_BNO08x.h>

// State definitions
bool imuReady = false;
float imuYaw = 0.0f;
float imuPitch = 0.0f;
float imuRoll = 0.0f;
float imuQuatReal = 1.0f;
float imuQuatI = 0.0f;
float imuQuatJ = 0.0f;
float imuQuatK = 0.0f;
unsigned long lastImuUpdate = 0;

// Use -1 for constructor reset_pin so Adafruit library doesn't execute its rushed 10ms reset.
// Hardware reset is handled explicitly with proper SH-2 bootloader delay (200ms).
static Adafruit_BNO08x bno08x(-1);
static sh2_SensorValue_t sensorValue;

// ============================================================
// IMU SETUP (Dedicated Wire1 hardware bus: SDA=40, SCL=41)
// ============================================================
void setupIMU() {
  serialPrintln("Starting BNO08x IMU on Wire1 (SDA=40, SCL=41)...");

  // 1. Hardware Reset: BNO08x Hillcrest firmware requires >= 150-200ms to boot from reset
  pinMode(IMU_RST, OUTPUT);
  digitalWrite(IMU_RST, HIGH);
  delay(10);
  digitalWrite(IMU_RST, LOW);
  delay(20);
  digitalWrite(IMU_RST, HIGH);
  delay(200); // Allow sensor processor to boot before I2C initialization

  // 2. Initialize secondary I2C hardware bus on ESP32-S3 (100kHz standard mode for clock stretching)
  Wire1.begin(IMU_SDA, IMU_SCL);
  Wire1.setClock(100000);

  // 3. Connect directly to verified address (0x4A) with quick retry and fallback
  bool connected = false;
  for (int attempt = 1; attempt <= 3; attempt++) {
    if (bno08x.begin_I2C(IMU_I2C_ADDR, &Wire1)) {
      connected = true;
      break;
    }
    delay(50);
  }

  // Fallback to alternate address 0x4B if 0x4A did not respond
  if (!connected) {
    uint8_t altAddr = (IMU_I2C_ADDR == 0x4A) ? 0x4B : 0x4A;
    if (bno08x.begin_I2C(altAddr, &Wire1)) {
      connected = true;
    }
  }

  if (!connected) {
    serialPrintln("WARNING: BNO08x IMU not detected (continuing without IMU)");
    imuReady = false;
    return;
  }

  serialPrintln("BNO08x IMU connected!");

  // 4. Enable Game Rotation Vector (quaternions fused from gyro & accel, immune to magnetic distortion)
  if (!bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, IMU_REPORT_INTERVAL_US)) {
    serialPrintln("WARNING: Could not enable BNO08x game rotation vector");
  }

  imuReady = true;
}

// ============================================================
// IMU UPDATE LOOP (NON-BLOCKING EVENT POLL)
// ============================================================
void updateIMU() {
  if (!imuReady) return;

  // Detect hardware/watchdog reset and restore report subscription
  if (bno08x.wasReset()) {
    serialPrintln("BNO08x reset detected! Restoring reports...");
    bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, IMU_REPORT_INTERVAL_US);
  }

  while (bno08x.getSensorEvent(&sensorValue)) {
    if (sensorValue.sensorId == SH2_GAME_ROTATION_VECTOR) {
      imuQuatReal = sensorValue.un.gameRotationVector.real;
      imuQuatI    = sensorValue.un.gameRotationVector.i;
      imuQuatJ    = sensorValue.un.gameRotationVector.j;
      imuQuatK    = sensorValue.un.gameRotationVector.k;

      // Convert quaternion (w, x, y, z) = (real, i, j, k) to Euler angles (degrees)
      float w = imuQuatReal;
      float x = imuQuatI;
      float y = imuQuatJ;
      float z = imuQuatK;

      // Roll (x-axis rotation: [-180..180])
      float sinr_cosp = 2.0f * (w * x + y * z);
      float cosr_cosp = 1.0f - 2.0f * (x * x + y * y);
      imuRoll = atan2f(sinr_cosp, cosr_cosp) * (180.0f / PI);

      // Pitch (y-axis rotation: [-90..90])
      float sinp = 2.0f * (w * y - z * x);
      if (fabsf(sinp) >= 1.0f) {
        imuPitch = copysignf(90.0f, sinp);
      } else {
        imuPitch = asinf(sinp) * (180.0f / PI);
      }

      // Yaw (z-axis rotation: [-180..180])
      float siny_cosp = 2.0f * (w * z + x * y);
      float cosy_cosp = 1.0f - 2.0f * (y * y + z * z);
      imuYaw = atan2f(siny_cosp, cosy_cosp) * (180.0f / PI);

      lastImuUpdate = millis();
    }
  }
}
