#include "imu.h"
#include "../config/config.h"
#include "../comm/serial_comm.h"
#include <Wire.h>
#include <Adafruit_BNO08x.h>
#include <esp_system.h>

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
// Adafruit's I2C HAL does not populate its t_us output. SH2 then builds
// timestamps around zero (observed on Cubey as 4294965xxx, often regressing).
// Supply the host acquisition time before SH2 applies the report's own delay.
// Keep this adapter in the sketch rather than modifying the installed library.
class TimestampedBNO08x : public Adafruit_BNO08x {
 public:
  TimestampedBNO08x() : Adafruit_BNO08x(-1) {}
 protected:
  bool _init(int32_t sensor_id) override {
    originalRead = _HAL.read;
    _HAL.read = timedRead;
    return Adafruit_BNO08x::_init(sensor_id);
  }
 private:
  static int (*originalRead)(sh2_Hal_t *, uint8_t *, unsigned, uint32_t *);
  static int timedRead(sh2_Hal_t *hal, uint8_t *buffer, unsigned length, uint32_t *timestamp) {
    *timestamp = micros();
    return originalRead(hal, buffer, length, timestamp);
  }
};
int (*TimestampedBNO08x::originalRead)(sh2_Hal_t *, uint8_t *, unsigned, uint32_t *) = nullptr;
static TimestampedBNO08x bno08x;
static sh2_SensorValue_t sensorValue;
static bool imuLinkUp = false;
static bool reportEnabled = false;
static uint32_t imuBoot = 0;
static uint32_t imuEpoch = 0;
static uint32_t imuSequence = 0;
static uint64_t imuSampleUs = 0;
static uint8_t imuAccuracy = 0;
static unsigned long lastImuPush = 0;
static unsigned long lastReportRetry = 0;
static unsigned long lastImuProbe = 0;

void sendIMUSnapshot(bool usb) {
  char packet[220];
  const bool fresh = imuReady && millis() - lastImuUpdate <= 200;
  snprintf(packet, sizeof(packet),
    "IMU:ok=%u,boot=%lu,epoch=%lu,seq=%lu,t_us=%llu,age_ms=%lu,cal=%u,qw=%.6f,qx=%.6f,qy=%.6f,qz=%.6f",
    fresh ? 1 : 0, (unsigned long)imuBoot, (unsigned long)imuEpoch,
    (unsigned long)imuSequence, (unsigned long long)imuSampleUs,
    millis() - lastImuUpdate, imuAccuracy,
    imuQuatReal, imuQuatI, imuQuatJ, imuQuatK);
  Serial1.println(packet);
  if (usb) Serial.println(packet);
}

// ============================================================
// IMU SETUP (Dedicated Wire1 hardware bus: SDA=40, SCL=41)
// ============================================================
// Attach the BNO08x from scratch. Safe to call again after a failed probe:
// the sensor is hardware-reset and re-addressed, which also drops any previous
// report subscription.
static bool attachImu() {
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
  for (int attempt = 1; attempt <= 3; attempt++) {
    if (bno08x.begin_I2C(IMU_I2C_ADDR, &Wire1)) {
      return true;
    }
    delay(50);
  }

  // Fallback to alternate address 0x4B if 0x4A did not respond
  uint8_t altAddr = (IMU_I2C_ADDR == 0x4A) ? 0x4B : 0x4A;
  return bno08x.begin_I2C(altAddr, &Wire1);
}

// 4. Enable Game Rotation Vector (quaternions fused from gyro & accel, immune
// to magnetic distortion). Log the failure because reports never arriving is
// otherwise indistinguishable from a sensor that never attached.
static void enableImuReports() {
  reportEnabled = bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, IMU_REPORT_INTERVAL_US);
  if (!reportEnabled) {
    serialPrintln("WARNING: Could not enable BNO08x game rotation vector");
  }
}

bool imuConnected() {
  return imuLinkUp;
}

void setupIMU() {
  imuBoot = esp_random();
  serialPrintln("Starting BNO08x IMU on Wire1 (SDA=40, SCL=41)...");

  imuLinkUp = attachImu();
  if (!imuLinkUp) {
    serialPrintln("WARNING: BNO08x IMU not detected (retrying while stopped)");
    imuReady = false;
    return;
  }

  serialPrintln("BNO08x IMU connected!");
  enableImuReports();

  imuReady = false; // A detected device is not yet a fresh orientation sample.
}

// ============================================================
// IMU UPDATE LOOP (NON-BLOCKING EVENT POLL)
// ============================================================
void updateIMU() {
  const unsigned long now = millis();
  const bool linkStale = imuLinkUp && now - lastImuUpdate > IMU_LINK_STALE_MS;
  if (!imuLinkUp || linkStale) {
    // A boot probe can miss the sensor, and an established link can stop
    // delivering samples. Re-probe only while stationary: the reset/boot delay
    // in attachImu() would otherwise stall motor and cliff supervision.
    if (!motorsRunning && now - lastImuProbe >= IMU_RETRY_INTERVAL_MS) {
      lastImuProbe = now;
      imuLinkUp = attachImu();
      reportEnabled = false;
      if (imuLinkUp) {
        ++imuEpoch; // New link/clock generation; the Pi resets its mapping.
        imuSequence = 0;
        imuSampleUs = 0;
        serialPrintln("BNO08x IMU link established; enabling reports...");
        enableImuReports();
        sendIMUSnapshot();
      }
    }
    if (!imuLinkUp) return;
  }

  if (now - lastImuUpdate > 200) imuReady = false;

  // Detect hardware/watchdog reset and restore report subscription
  if (bno08x.wasReset()) {
    imuReady = false;
    ++imuEpoch;
    imuSampleUs = 0;
    serialPrintln("BNO08x reset detected! Restoring reports...");
    enableImuReports();
    sendIMUSnapshot();
  }

  if (!reportEnabled && millis() - lastReportRetry > 1000) {
    lastReportRetry = millis();
    reportEnabled = bno08x.enableReport(SH2_GAME_ROTATION_VECTOR, IMU_REPORT_INTERVAL_US);
  }

  bool newSample = false;
  // Bound work so a sensor backlog cannot starve motor/cliff supervision.
  for (int events = 0; events < 4 && bno08x.getSensorEvent(&sensorValue); ++events) {
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
      float norm = sqrtf(w*w + x*x + y*y + z*z);
      if (!isfinite(norm) || norm < 0.8f || norm > 1.2f) {
        imuReady = false;
        continue;
      }
      w /= norm; x /= norm; y /= norm; z /= norm;
      imuQuatReal = w; imuQuatI = x; imuQuatJ = y; imuQuatK = z;

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
      imuSampleUs = sensorValue.timestamp;
      imuAccuracy = sensorValue.status & 3;
      ++imuSequence;
      imuReady = true;
      newSample = true;
    }
  }
  if (newSample || millis() - lastImuPush >= 250) {
    lastImuPush = millis();
    sendIMUSnapshot();
  }
}
