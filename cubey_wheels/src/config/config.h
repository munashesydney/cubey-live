#pragma once

#include <Arduino.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>

// ============================================================
// CUBEY — VERIFIED 4-WHEEL MECANUM CONTROLLER CONFIGURATION
// ============================================================

enum Motion {
  STOPPED,
  FORWARD,
  BACKWARD,
  STRAFE_LEFT,
  STRAFE_RIGHT,
  ROTATE_LEFT,
  ROTATE_RIGHT,
  FORWARD_LEFT,
  FORWARD_RIGHT,
  BACKWARD_LEFT,
  BACKWARD_RIGHT,
  VELOCITY_CONTROL
};

// ---------------- Wi-Fi ----------------
constexpr const char* WIFI_NAME = "Cubey-Control";
constexpr const char* WIFI_PASSWORD = "cubey123";

// ---------------- Cliff sensors ----------------
#define SENSOR_SDA 6
#define SENSOR_SCL 7

#define FRONT_XSHUT 48
#define BACK_XSHUT  47

// ---------------- Onboard Status RGB LED ----------------
#ifndef RGB_BUILTIN
#define RGB_BUILTIN 48
#endif

// ---------------- Battery Voltage & Charging Sensing ----------------
#define BATT_ADC_PIN 20
constexpr float BATT_DIVIDER_RATIO = 3.0f; // R1=20k (2x10k), R2=10k -> (20k+10k)/10k = 3.0

constexpr unsigned long TELEMETRY_PUSH_INTERVAL_MS = 250; // Push 4 times a second (250ms) for high-speed responsiveness
constexpr unsigned long CHARGING_DIAGNOSTIC_INTERVAL_MS = 5000;

// Cliff distance threshold & confirmation readings
constexpr uint16_t CLIFF_DISTANCE_MM = 140;
constexpr int CLIFF_CONFIRM_READINGS = 2;
constexpr unsigned long SENSOR_INTERVAL_MS = 50;

constexpr int SAFETY_ESCAPE_SPEED = 130;
constexpr unsigned long SAFETY_ESCAPE_TIME_MS = 280;

// ---------------- Verified GPIO mapping ----------------
#define STBY1 4
#define STBY2 5

// REAL FRONT RIGHT
#define FRONT_RIGHT_IN1 15
#define FRONT_RIGHT_IN2 16
#define FRONT_RIGHT_PWM 17

// REAL FRONT LEFT
#define FRONT_LEFT_IN1 18
#define FRONT_LEFT_IN2 8
#define FRONT_LEFT_PWM 9

// REAL BACK RIGHT
#define BACK_RIGHT_IN1 10
#define BACK_RIGHT_IN2 11
#define BACK_RIGHT_PWM 12

// REAL BACK LEFT
#define BACK_LEFT_IN1 13
#define BACK_LEFT_IN2 14
#define BACK_LEFT_PWM 21

// ---------------- Calibration ----------------
// Verified: BACK LEFT electrical direction is reversed.
// The other three are normal.
constexpr bool INVERT_FRONT_LEFT  = true;
constexpr bool INVERT_FRONT_RIGHT = false;
constexpr bool INVERT_BACK_LEFT   = true;
constexpr bool INVERT_BACK_RIGHT  = true;

// false = normal X-style mecanum pattern.
// true  = opposite roller orientation.
// Start with false. If sideways and rotation are swapped, change to true.
constexpr bool OPPOSITE_ROLLER_LAYOUT = false;

// ---------------- Motion settings ----------------
constexpr unsigned long COMMAND_TIMEOUT_MS = 700;

// ---------------- Serial & Telemetry Settings ----------------
#define RPI_RX_PIN 2   // Connect to Raspberry Pi Pin 8 (GPIO14 / TXD)
#define RPI_TX_PIN 1   // Connect to Raspberry Pi Pin 10 (GPIO15 / RXD)

constexpr unsigned long TELEMETRY_INTERVAL_MS = 250;

// ============================================================
// SHARED SYSTEM STATE (EXTERN DECLARATIONS)
// ============================================================

// Motion & Motor state
extern Motion currentMotion;
extern int motorSpeed;
extern int twistForward;
extern int twistLeft;
extern int twistCounterClockwise;
extern bool motorsRunning;
extern bool emergencyStopLatched;
extern unsigned long lastCommandTime;

// Cliff sensor state
extern Adafruit_VL53L0X frontSensor;
extern Adafruit_VL53L0X backSensor;
extern bool frontSensorReady;
extern bool backSensorReady;
extern bool frontCliff;
extern bool backCliff;
extern int frontCliffCount;
extern int backCliffCount;
extern unsigned long lastSensorCheck;
extern bool safetyMovementRunning;

// Battery & Charging state
extern float lastRestingVoltage;
extern bool isCharging;
extern unsigned long lastTelemetryPush;
extern unsigned long lastChargingDiagnosticTime;

// Serial buffers & timing
extern unsigned long lastTelemetryTime;
extern String serialRxBuffer;
extern String serial0RxBuffer;
extern String serial1RxBuffer;

// Web server
extern WebServer server;
