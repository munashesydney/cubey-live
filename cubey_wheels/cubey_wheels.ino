#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>

// ============================================================
// CUBEY — VERIFIED 4-WHEEL MECANUM CONTROLLER
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

// Motion state is used by battery/charging logic defined near the top of the
// sketch, so it must be declared before those functions are compiled.
Motion currentMotion = STOPPED;

// Forward declarations
const char* motionToString(Motion motion);
void printMotion(Motion motion);
void applyMotion(Motion motion);
void applyTwistCommand(int forward, int left, int counterClockwise);
bool movesTowardFront(Motion motion);
bool movesTowardBack(Motion motion);
void serialPrint(const String &s);
void serialPrintln(const String &s);
void reportChargingDiagnostic(
  float batteryVoltage,
  float baselineVoltage,
  float deltaVoltage,
  const char* reason
);

// ---------------- Wi-Fi ----------------
const char* WIFI_NAME = "Cubey-Control";
const char* WIFI_PASSWORD = "cubey123";

WebServer server(80);

// ---------------- Cliff sensors ----------------
#define SENSOR_SDA 6
#define SENSOR_SCL 7

#define FRONT_XSHUT 48
#define BACK_XSHUT  47

// ---------------- Onboard Status RGB LED ----------------
#ifndef RGB_BUILTIN
#define RGB_BUILTIN 48
#endif

// ---------------- Battery Voltage Sensing ----------------
// ---------------- Battery Voltage & Charging Sensing ----------------
#define BATT_ADC_PIN 20
const float BATT_DIVIDER_RATIO = 3.0f; // R1=20k (2x10k), R2=10k -> (20k+10k)/10k = 3.0

float lastRestingVoltage = 0.0f;
bool isCharging = false;
unsigned long lastTelemetryPush = 0;
const unsigned long TELEMETRY_PUSH_INTERVAL_MS = 250; // Push 4 times a second (250ms) for high-speed responsiveness
unsigned long lastChargingDiagnosticTime = 0;
const unsigned long CHARGING_DIAGNOSTIC_INTERVAL_MS = 5000;

float readBatteryVoltage() {
  uint32_t sumMv = 0;
  const int samples = 32;
  for (int i = 0; i < samples; i++) {
    sumMv += analogReadMilliVolts(BATT_ADC_PIN);
    delayMicroseconds(25);
  }
  float adcV = (sumMv / (float)samples) / 1000.0f;
  float battV = adcV * BATT_DIVIDER_RATIO;
  float baselineBefore = lastRestingVoltage;
  float deltaV = baselineBefore > 5.0f ? battV - baselineBefore : 0.0f;
  bool previousCharging = isCharging;
  const char* detectionReason = "monitoring";

  // On-chip Charging Detection (Step detection & High float threshold)
  if (battV >= 8.32f) {
    isCharging = true;
    detectionReason = "high_voltage";
  } else if (currentMotion == STOPPED) {
    if (lastRestingVoltage > 5.0f && (battV - lastRestingVoltage) >= 0.08f) {
      isCharging = true;
      detectionReason = "voltage_step_up";
    } else if (battV < 8.15f && lastRestingVoltage > 5.0f && (lastRestingVoltage - battV) >= 0.05f) {
      isCharging = false;
      detectionReason = "voltage_step_down";
    }
    if (lastRestingVoltage < 5.0f) {
      lastRestingVoltage = battV;
    } else {
      lastRestingVoltage = lastRestingVoltage * 0.95f + battV * 0.05f;
    }
  } else {
    detectionReason = "motion_active";
  }

  unsigned long now = millis();
  if (
    isCharging != previousCharging ||
    now - lastChargingDiagnosticTime >= CHARGING_DIAGNOSTIC_INTERVAL_MS
  ) {
    lastChargingDiagnosticTime = now;
    reportChargingDiagnostic(
      battV,
      baselineBefore,
      deltaV,
      detectionReason
    );
  }
  return battV;
}

int calculateBatteryPercent(float voltage) {
  // 2S Li-ion discharge curve:
  // 8.40V -> 100%
  // 8.00V -> 80%
  // 7.60V -> 50%
  // 7.20V -> 20%
  // 6.60V -> 0%
  if (voltage >= 8.40f) return 100;
  if (voltage <= 6.60f) return 0;

  if (voltage >= 8.00f) {
    return 80 + (int)((voltage - 8.00f) / (8.40f - 8.00f) * 20.0f);
  } else if (voltage >= 7.60f) {
    return 50 + (int)((voltage - 7.60f) / (8.00f - 7.60f) * 30.0f);
  } else if (voltage >= 7.20f) {
    return 20 + (int)((voltage - 7.20f) / (7.60f - 7.20f) * 30.0f);
  } else {
    return (int)((voltage - 6.60f) / (7.20f - 6.60f) * 20.0f);
  }
}

Adafruit_VL53L0X frontSensor;
Adafruit_VL53L0X backSensor;

bool frontSensorReady = false;
bool backSensorReady = false;

bool frontCliff = false;
bool backCliff = false;

// Change this after checking the normal floor reading.
// Example: normal floor = 55 mm, cliff = 300+ mm.
// 140 mm is a reasonable starting point.
const uint16_t CLIFF_DISTANCE_MM = 140;

// Require multiple bad readings to avoid reacting to one glitch.
const int CLIFF_CONFIRM_READINGS = 2;

int frontCliffCount = 0;
int backCliffCount = 0;

unsigned long lastSensorCheck = 0;
const unsigned long SENSOR_INTERVAL_MS = 50;

bool safetyMovementRunning = false;

const int SAFETY_ESCAPE_SPEED = 130;
const unsigned long SAFETY_ESCAPE_TIME_MS = 280;

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
const bool INVERT_FRONT_LEFT  = true;
const bool INVERT_FRONT_RIGHT = false;
const bool INVERT_BACK_LEFT   = true;
const bool INVERT_BACK_RIGHT  = true;

// false = normal X-style mecanum pattern.
// true  = opposite roller orientation.
// Start with false. If sideways and rotation are swapped, change to true.
const bool OPPOSITE_ROLLER_LAYOUT = false;

// ---------------- Motion settings ----------------
int motorSpeed = 180;
int twistForward = 0;
int twistLeft = 0;
int twistCounterClockwise = 0;

const unsigned long COMMAND_TIMEOUT_MS = 700;
unsigned long lastCommandTime = 0;
bool motorsRunning = false;
bool emergencyStopLatched = false;

// ---------------- Serial & Telemetry Settings ----------------
#define RPI_RX_PIN 2   // Connect to Raspberry Pi Pin 8 (GPIO14 / TXD)
#define RPI_TX_PIN 1   // Connect to Raspberry Pi Pin 10 (GPIO15 / RXD)

unsigned long lastTelemetryTime = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 250;
String serialRxBuffer = "";
String serial0RxBuffer = "";
String serial1RxBuffer = "";

// Forward declarations
void setupPins();
void setupCliffSensors();
void setupWebServer();
void updateCliffSafety();
void stopAll();
void applyMotion(Motion motion);
void driveWheels(int fl, int fr, int bl, int br);
void driveWheelPowers(int fl, int fr, int bl, int br);
void setMotor(int in1, int in2, int pwmPin, int logicalDirection, bool inverted);
void performSafetyEscape(bool moveForward);
bool movesTowardFront(Motion motion);
bool movesTowardBack(Motion motion);
void sendTelemetry(bool broadcast);
void processSerialCommands();
void handleIncomingLine(String line);
void handleCommandName(String command);
void testSingleMotor(String motorName, int direction, int speed);
const char* motionToString(Motion motion);
void serialPrint(const String &s);
void serialPrintln(const String &s);
void serialPrintln();
void setStatusLed(uint8_t r, uint8_t g, uint8_t b);

// ============================================================
// WEB PAGE
// ============================================================
const char webpage[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta
    name="viewport"
    content="width=device-width, initial-scale=1,
             maximum-scale=1, user-scalable=no"
  >
  <meta name="apple-mobile-web-app-capable" content="yes">
  <title>Cubey Controller</title>

  <style>
    * {
      box-sizing: border-box;
      user-select: none;
      -webkit-user-select: none;
      -webkit-touch-callout: none;
      touch-action: manipulation;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: #0e0e13;
      color: white;
      font-family: Arial, sans-serif;
      overflow: hidden;
    }

    .app {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 22px;
      padding: 18px;
    }

    .side {
      width: 190px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .title {
      text-align: center;
      font-size: 36px;
      font-weight: bold;
    }

    #status {
      min-height: 24px;
      text-align: center;
      color: #aaaab5;
      font-size: 17px;
    }

    .dpad {
      width: min(72vh, 500px);
      height: min(72vh, 500px);
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      grid-template-rows: repeat(3, 1fr);
      gap: 11px;
    }

    button {
      border: none;
      border-radius: 20px;
      background: #32323b;
      color: white;
      font-size: 34px;
      font-weight: bold;
      box-shadow: 0 5px 0 #18181e;
    }

    button:active,
    button.active {
      transform: translateY(4px);
      background: #595969;
      box-shadow: 0 1px 0 #18181e;
    }

    .diagonal {
      background: #25252d;
      font-size: 30px;
    }

    .stop {
      background: #d52b3e;
      box-shadow: 0 5px 0 #741721;
      font-size: 22px;
    }

    .rotate {
      height: 110px;
      background: #3a3a45;
      font-size: 19px;
    }

    .rotate-symbol {
      display: block;
      font-size: 42px;
      margin-bottom: 4px;
    }

    .speed-box {
      background: #1c1c23;
      border-radius: 18px;
      padding: 17px;
    }

    .speed-label {
      text-align: center;
      margin-bottom: 12px;
      font-size: 17px;
    }

    input[type="range"] {
      width: 100%;
      height: 35px;
    }

    .help {
      color: #8d8d98;
      font-size: 14px;
      text-align: center;
      line-height: 1.5;
    }

    @media (orientation: portrait) {
      body {
        overflow: auto;
      }

      .app {
        flex-direction: column;
      }

      .side {
        width: min(92vw, 500px);
      }

      .dpad {
        width: min(92vw, 500px);
        height: min(92vw, 500px);
      }

      .rotate {
        height: 80px;
      }
    }
  </style>
</head>

<body>
  <div class="app">
    <div class="side">
      <div class="title">Cubey</div>
      <div id="status">Stopped</div>

      <button class="movement rotate" data-command="rotateLeft">
        <span class="rotate-symbol">↺</span>
        Rotate left
      </button>

      <div class="speed-box">
        <div class="speed-label">
          Speed: <span id="speedValue">180</span>
        </div>

        <input
          id="speedSlider"
          type="range"
          min="70"
          max="255"
          value="180"
        >
      </div>
    </div>

    <div class="dpad">
      <button class="movement diagonal" data-command="forwardLeft">↖</button>
      <button class="movement" data-command="forward">↑</button>
      <button class="movement diagonal" data-command="forwardRight">↗</button>

      <button class="movement" data-command="strafeLeft">←</button>
      <button class="stop" id="stopButton">STOP</button>
      <button class="movement" data-command="strafeRight">→</button>

      <button class="movement diagonal" data-command="backwardLeft">↙</button>
      <button class="movement" data-command="backward">↓</button>
      <button class="movement diagonal" data-command="backwardRight">↘</button>
    </div>

    <div class="side">
      <button class="movement rotate" data-command="rotateRight">
        <span class="rotate-symbol">↻</span>
        Rotate right
      </button>

      <div class="help">
        Hold to move.<br>
        Release to stop.<br><br>
        ← and → strafe.<br>
        ↺ and ↻ rotate.
      </div>
    </div>
  </div>

  <script>
    let activeButton = null;
    let repeatTimer = null;

    const statusElement = document.getElementById("status");

    const labels = {
      forward: "Forward",
      backward: "Backward",
      strafeLeft: "Strafing left",
      strafeRight: "Strafing right",
      rotateLeft: "Rotating left",
      rotateRight: "Rotating right",
      forwardLeft: "Forward-left",
      forwardRight: "Forward-right",
      backwardLeft: "Backward-left",
      backwardRight: "Backward-right"
    };

    function sendCommand(command) {
      fetch("/command?name=" + encodeURIComponent(command), {
        method: "GET",
        cache: "no-store"
      }).catch(function(error) {
        console.log(error);
      });
    }

    function stopMovement(sendStop = true) {
      if (repeatTimer !== null) {
        clearInterval(repeatTimer);
        repeatTimer = null;
      }

      if (activeButton !== null) {
        activeButton.classList.remove("active");
        activeButton = null;
      }

      if (sendStop) {
        sendCommand("stop");
      }

      statusElement.innerText = "Stopped";
    }

    function startMovement(command, button, event) {
      event.preventDefault();
      stopMovement(false);

      activeButton = button;
      activeButton.classList.add("active");
      statusElement.innerText = labels[command] || command;

      try {
        button.setPointerCapture(event.pointerId);
      } catch (error) {
      }

      sendCommand(command);

      repeatTimer = setInterval(function() {
        sendCommand(command);
      }, 200);
    }

    document.querySelectorAll(".movement").forEach(function(button) {
      button.addEventListener("pointerdown", function(event) {
        startMovement(button.dataset.command, button, event);
      });

      button.addEventListener("pointerup", function() {
        stopMovement();
      });

      button.addEventListener("pointercancel", function() {
        stopMovement();
      });
    });

    document.getElementById("stopButton")
      .addEventListener("pointerdown", function(event) {
        event.preventDefault();
        stopMovement();
      });

    const speedSlider = document.getElementById("speedSlider");

    speedSlider.addEventListener("input", function() {
      document.getElementById("speedValue").innerText =
        speedSlider.value;

      fetch("/speed?value=" + speedSlider.value, {
        cache: "no-store"
      }).catch(function(error) {
        console.log(error);
      });
    });

    window.addEventListener("blur", function() {
      stopMovement();
    });

    document.addEventListener("visibilitychange", function() {
      if (document.hidden) {
        stopMovement();
      }
    });

    document.addEventListener("contextmenu", function(event) {
      event.preventDefault();
    });
  </script>
</body>
</html>
)rawliteral";

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1, RPI_RX_PIN, RPI_TX_PIN);
  #if defined(ESP32)
  Serial0.begin(115200);
  #endif
  delay(1000);

  // Set gentle, low-intensity warm orange status LED
  setStatusLed(20, 6, 0);

  setupPins();

  digitalWrite(STBY1, HIGH);
  digitalWrite(STBY2, HIGH);

  stopAll();

  // Start front and back cliff sensors
  setupCliffSensors();

  WiFi.mode(WIFI_AP);

  if (!WiFi.softAP(WIFI_NAME, WIFI_PASSWORD)) {
    serialPrintln("ERROR: Wi-Fi access point failed");
  }

  setupWebServer();
  server.begin();

  float bV = readBatteryVoltage();
  int bPct = calculateBatteryPercent(bV);

  serialPrintln();
  serialPrintln("====================================");
  serialPrintln("Cubey mecanum controller ready");
  serialPrint("Battery: ");
  serialPrint(String(bV, 2));
  serialPrint(" V (");
  serialPrint(String(bPct));
  serialPrintln("%)");
  serialPrint("RPi Dedicated UART: RX=GPIO ");
  serialPrint(String(RPI_RX_PIN));
  serialPrint(" | TX=GPIO ");
  serialPrintln(String(RPI_TX_PIN));
  serialPrintln("Also listening on: Serial (USB) + Serial0");
  serialPrint("Wi-Fi: ");
  serialPrintln(WIFI_NAME);
  serialPrint("Password: ");
  serialPrintln(WIFI_PASSWORD);
  serialPrint("Open: http://");
  serialPrintln(WiFi.softAPIP().toString());
  serialPrintln("====================================");
}

// ============================================================
// LOOP
// ============================================================
void loop() {
  server.handleClient();
  processSerialCommands();
  updateCliffSafety();

  // Autonomous high-speed telemetry push (4 times a second)
  if (millis() - lastTelemetryPush >= TELEMETRY_PUSH_INTERVAL_MS) {
    lastTelemetryPush = millis();
    sendTelemetry(false);
  }

  if (
    motorsRunning &&
    !safetyMovementRunning &&
    millis() - lastCommandTime > COMMAND_TIMEOUT_MS
  ) {
    stopAll();
  }
}

// ============================================================
// GPIO
// ============================================================
void setupPins() {
  pinMode(BATT_ADC_PIN, INPUT);
  #if defined(ESP32)
  analogSetPinAttenuation(BATT_ADC_PIN, ADC_11db);
  #endif

  pinMode(STBY1, OUTPUT);
  pinMode(STBY2, OUTPUT);

  pinMode(BACK_LEFT_IN1, OUTPUT);
  pinMode(BACK_LEFT_IN2, OUTPUT);
  pinMode(BACK_LEFT_PWM, OUTPUT);

  pinMode(BACK_RIGHT_IN1, OUTPUT);
  pinMode(BACK_RIGHT_IN2, OUTPUT);
  pinMode(BACK_RIGHT_PWM, OUTPUT);

  pinMode(FRONT_LEFT_IN1, OUTPUT);
  pinMode(FRONT_LEFT_IN2, OUTPUT);
  pinMode(FRONT_LEFT_PWM, OUTPUT);

  pinMode(FRONT_RIGHT_IN1, OUTPUT);
  pinMode(FRONT_RIGHT_IN2, OUTPUT);
  pinMode(FRONT_RIGHT_PWM, OUTPUT);
}

// ============================================================
// SERIAL PRINT HELPERS (TRIPLE-STREAM: RPI UART, USB, UART0)
// ============================================================
void serialPrint(const String &s) {
  Serial.print(s);
  Serial1.print(s);
  #if defined(ESP32)
  Serial0.print(s);
  #endif
}

void serialPrintln(const String &s) {
  Serial.println(s);
  Serial1.println(s);
  #if defined(ESP32)
  Serial0.println(s);
  #endif
}

void serialPrintln() {
  Serial.println();
  Serial1.println();
  #if defined(ESP32)
  Serial0.println();
  #endif
}

void reportChargingDiagnostic(
  float batteryVoltage,
  float baselineVoltage,
  float deltaVoltage,
  const char* reason
) {
  String message = "CHARGE_DIAG:batt_v=";
  message += String(batteryVoltage, 3);
  message += ",baseline_v=";
  message += String(baselineVoltage, 3);
  message += ",delta_v=";
  message += String(deltaVoltage, 3);
  message += ",motion=";
  message += motionToString(currentMotion);
  message += ",charging=";
  message += isCharging ? "1" : "0";
  message += ",reason=";
  message += reason;
  serialPrintln(message);
}

// ============================================================
// STATUS RGB LED (SOFT / DIM ORANGE)
// ============================================================
void setStatusLed(uint8_t r, uint8_t g, uint8_t b) {
  // Several ESP32-S3 boards place the RGB LED on GPIO48, which is also
  // Cubey's front sensor XSHUT pin. Never drive the LED on that hardware.
  #if defined(ESP32) && RGB_BUILTIN != FRONT_XSHUT
  neopixelWrite(RGB_BUILTIN, r, g, b);
  #endif
}

// ============================================================
// COMMAND HANDLERS
// ============================================================
void handleCommandName(String command) {
  command.trim();
  if (command == "forward") {
    applyMotion(FORWARD);
  }
  else if (command == "backward") {
    applyMotion(BACKWARD);
  }
  else if (command == "strafeLeft") {
    applyMotion(STRAFE_LEFT);
  }
  else if (command == "strafeRight") {
    applyMotion(STRAFE_RIGHT);
  }
  else if (command == "rotateLeft") {
    applyMotion(ROTATE_LEFT);
  }
  else if (command == "rotateRight") {
    applyMotion(ROTATE_RIGHT);
  }
  else if (command == "forwardLeft") {
    applyMotion(FORWARD_LEFT);
  }
  else if (command == "forwardRight") {
    applyMotion(FORWARD_RIGHT);
  }
  else if (command == "backwardLeft") {
    applyMotion(BACKWARD_LEFT);
  }
  else if (command == "backwardRight") {
    applyMotion(BACKWARD_RIGHT);
  }
  else if (command == "stop") {
    applyMotion(STOPPED);
  }
  else {
    serialPrint("ERR: Unknown command '");
    serialPrint(command);
    serialPrintln("'");
  }
}

void testSingleMotor(String motorName, int direction, int speed) {
  stopAll();
  if (emergencyStopLatched && direction != 0) {
    serialPrintln("SAFETY: Emergency stop latched - motor test blocked");
    return;
  }
  motorName.toLowerCase();
  motorName.trim();

  if (speed > 0) {
    motorSpeed = constrain(speed, 70, 255);
  }

  if (motorName == "fl") {
    setMotor(FRONT_LEFT_IN1, FRONT_LEFT_IN2, FRONT_LEFT_PWM, direction, INVERT_FRONT_LEFT);
  }
  else if (motorName == "fr") {
    setMotor(FRONT_RIGHT_IN1, FRONT_RIGHT_IN2, FRONT_RIGHT_PWM, direction, INVERT_FRONT_RIGHT);
  }
  else if (motorName == "bl") {
    setMotor(BACK_LEFT_IN1, BACK_LEFT_IN2, BACK_LEFT_PWM, direction, INVERT_BACK_LEFT);
  }
  else if (motorName == "br") {
    setMotor(BACK_RIGHT_IN1, BACK_RIGHT_IN2, BACK_RIGHT_PWM, direction, INVERT_BACK_RIGHT);
  }
  else {
    serialPrint("ERR: Unknown motor '");
    serialPrint(motorName);
    serialPrintln("'");
    return;
  }

  motorsRunning = (direction != 0);
  if (motorsRunning) {
    lastCommandTime = millis();
  }
}

const char* motionToString(Motion motion) {
  switch (motion) {
    case FORWARD: return "FORWARD";
    case BACKWARD: return "BACKWARD";
    case STRAFE_LEFT: return "STRAFE_LEFT";
    case STRAFE_RIGHT: return "STRAFE_RIGHT";
    case ROTATE_LEFT: return "ROTATE_LEFT";
    case ROTATE_RIGHT: return "ROTATE_RIGHT";
    case FORWARD_LEFT: return "FORWARD_LEFT";
    case FORWARD_RIGHT: return "FORWARD_RIGHT";
    case BACKWARD_LEFT: return "BACKWARD_LEFT";
    case BACKWARD_RIGHT: return "BACKWARD_RIGHT";
    case VELOCITY_CONTROL: return "TWIST";
    case STOPPED:
    default: return "STOPPED";
  }
}

void sendTelemetry(bool broadcast) {
  uint16_t frontDistance = 0;
  uint16_t backDistance = 0;
  if (frontSensorReady) {
    readFloorSensor(frontSensor, frontDistance);
  }
  if (backSensorReady) {
    readFloorSensor(backSensor, backDistance);
  }
  float bV = readBatteryVoltage();
  int bPct = calculateBatteryPercent(bV);

  String msg = "TELEMETRY:front_dist=" + String(frontDistance) +
               ",back_dist=" + String(backDistance) +
               ",front_cliff=" + (frontCliff ? "1" : "0") +
               ",back_cliff=" + (backCliff ? "1" : "0") +
               ",motion=" + String(motionToString(currentMotion)) +
               ",speed=" + String(motorSpeed) +
               ",batt_v=" + String(bV, 2) +
               ",batt_pct=" + String(bPct) +
               ",charging=" + (isCharging ? "1" : "0");
  msg += ",estop=" + String(emergencyStopLatched ? "1" : "0");
  // The Pi consumes the continuous stream on the dedicated hardware UART.
  // Keep USB Serial and UART0 readable; they receive telemetry only when a
  // STATUS command explicitly requests a snapshot.
  Serial1.println(msg);
  if (broadcast) {
    Serial.println(msg);
    #if defined(ESP32)
    Serial0.println(msg);
    #endif
  }
}

// ============================================================
// SERIAL PROTOCOL PROCESSOR (READS FROM USB AND HARDWARE UART)
// ============================================================
void handleIncomingLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  if (line.equalsIgnoreCase("ESTOP")) {
    emergencyStopLatched = true;
    stopAll();
    serialPrintln("ACK:ESTOP");
  }
  else if (line.equalsIgnoreCase("RESET_ESTOP")) {
    // Reset only re-arms the controller. It never resumes the previous motion.
    stopAll();
    emergencyStopLatched = false;
    serialPrintln("ACK:RESET_ESTOP");
  }
  else if (line.startsWith("CMD:")) {
    String cmd = line.substring(4);
    cmd.trim();
    handleCommandName(cmd);
    serialPrintln("ACK:CMD:" + cmd);
  }
  else if (line.startsWith("SPEED:")) {
    int spd = line.substring(6).toInt();
    motorSpeed = constrain(spd, 70, 255);
    if (currentMotion != STOPPED) {
      applyMotion(currentMotion);
    }
    serialPrintln("ACK:SPEED:" + String(motorSpeed));
  }
  else if (line.startsWith("TWIST:")) {
    // Normalized mecanum axes: forward,left,counter-clockwise in -1000..1000.
    String rest = line.substring(6);
    int comma1 = rest.indexOf(',');
    int comma2 = rest.indexOf(',', comma1 + 1);
    if (comma1 <= 0 || comma2 <= comma1 + 1) {
      serialPrintln("ERR:TWIST_FORMAT");
    } else {
      int forward = rest.substring(0, comma1).toInt();
      int left = rest.substring(comma1 + 1, comma2).toInt();
      int counterClockwise = rest.substring(comma2 + 1).toInt();
      applyTwistCommand(forward, left, counterClockwise);
      // Do not ACK the high-rate control stream; telemetry is the heartbeat.
    }
  }
  else if (line.startsWith("MOTOR:")) {
    // format: MOTOR:<fl|fr|bl|br>,<direction>,<speed>
    String rest = line.substring(6);
    int comma1 = rest.indexOf(',');
    if (comma1 > 0) {
      String motorName = rest.substring(0, comma1);
      int comma2 = rest.indexOf(',', comma1 + 1);
      int dir = 0;
      int spd = motorSpeed;
      if (comma2 > 0) {
        dir = rest.substring(comma1 + 1, comma2).toInt();
        spd = rest.substring(comma2 + 1).toInt();
      } else {
        dir = rest.substring(comma1 + 1).toInt();
      }
      testSingleMotor(motorName, dir, spd);
      serialPrintln("ACK:MOTOR:" + motorName + "," + String(dir));
    }
  }
  else if (line.equalsIgnoreCase("PING")) {
    serialPrintln("PONG");
  }
  else if (line.equalsIgnoreCase("STATUS")) {
    sendTelemetry(true);
  }
  else {
    // Fallback: direct command name like "forward", "stop", etc.
    handleCommandName(line);
  }
}

void processSerialCommands() {
  // 1. Read from Dedicated RPi Hardware UART (GPIO 2 RX, GPIO 1 TX)
  while (Serial1.available() > 0) {
    char c = (char)Serial1.read();

    if (c == '\r') continue;
    if (c == '\n') {
      if (serial1RxBuffer.length() > 0) {
        serialPrintln("[ESP-RPI-CMD] " + serial1RxBuffer);
        handleIncomingLine(serial1RxBuffer);
      }
      serial1RxBuffer = "";
    } else {
      if (serial1RxBuffer.length() < 128) {
        serial1RxBuffer += c;
      }
    }
  }

  // 2. Read from USB CDC / Default Serial
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\r') continue;
    if (c == '\n') {
      if (serialRxBuffer.length() > 0) {
        serialPrintln("[ESP-USB-CMD] " + serialRxBuffer);
        handleIncomingLine(serialRxBuffer);
      }
      serialRxBuffer = "";
    } else {
      if (serialRxBuffer.length() < 128) {
        serialRxBuffer += c;
      }
    }
  }

  // 3. Read from Hardware UART0 (physical TX/RX header pins)
  #if defined(ESP32)
  while (Serial0.available() > 0) {
    char c = (char)Serial0.read();

    if (c == '\r') continue;
    if (c == '\n') {
      if (serial0RxBuffer.length() > 0) {
        serialPrintln("[ESP-UART0-CMD] " + serial0RxBuffer);
        handleIncomingLine(serial0RxBuffer);
      }
      serial0RxBuffer = "";
    } else {
      if (serial0RxBuffer.length() < 128) {
        serial0RxBuffer += c;
      }
    }
  }
  #endif
}

// ============================================================
// WEB ROUTES
// ============================================================
void setupWebServer() {
  server.on("/", HTTP_GET, []() {
    server.sendHeader(
      "Cache-Control",
      "no-store, no-cache, must-revalidate"
    );
    server.send_P(200, "text/html", webpage);
  });

  server.on("/command", HTTP_GET, []() {
    if (!server.hasArg("name")) {
      server.send(400, "text/plain", "Missing command");
      return;
    }

    String command = server.arg("name");
    handleCommandName(command);
    server.send(200, "text/plain", "OK");
  });

  server.on("/speed", HTTP_GET, []() {
    if (server.hasArg("value")) {
      motorSpeed = constrain(server.arg("value").toInt(), 0, 255);

      // Immediately reapply the current movement at the new speed.
      if (currentMotion != STOPPED) {
        applyMotion(currentMotion);
      }
    }

    server.send(200, "text/plain", String(motorSpeed));
  });

  server.onNotFound([]() {
    server.send(404, "text/plain", "Not found");
  });
}

// ============================================================
// MOTOR CONTROL
//
// Logical direction:
//   +1 = this physical wheel pushes Cubey forward
//   -1 = this physical wheel pushes Cubey backward
//    0 = stop
// ============================================================
void setMotor(
  int in1,
  int in2,
  int pwmPin,
  int logicalDirection,
  bool inverted
) {
  if (inverted) {
    logicalDirection = -logicalDirection;
  }

  if (logicalDirection > 0) {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
    analogWrite(pwmPin, motorSpeed);
  }
  else if (logicalDirection < 0) {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
    analogWrite(pwmPin, motorSpeed);
  }
  else {
    analogWrite(pwmPin, 0);
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);
  }
}

void setMotorPower(
  int in1,
  int in2,
  int pwmPin,
  int signedPower,
  bool inverted
) {
  signedPower = constrain(signedPower, -255, 255);
  if (inverted) {
    signedPower = -signedPower;
  }

  if (signedPower > 0) {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
    analogWrite(pwmPin, signedPower);
  }
  else if (signedPower < 0) {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
    analogWrite(pwmPin, -signedPower);
  }
  else {
    analogWrite(pwmPin, 0);
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);
  }
}

void driveWheels(int fl, int fr, int bl, int br) {
  setMotor(
    FRONT_LEFT_IN1,
    FRONT_LEFT_IN2,
    FRONT_LEFT_PWM,
    fl,
    INVERT_FRONT_LEFT
  );

  setMotor(
    FRONT_RIGHT_IN1,
    FRONT_RIGHT_IN2,
    FRONT_RIGHT_PWM,
    fr,
    INVERT_FRONT_RIGHT
  );

  setMotor(
    BACK_LEFT_IN1,
    BACK_LEFT_IN2,
    BACK_LEFT_PWM,
    bl,
    INVERT_BACK_LEFT
  );

  setMotor(
    BACK_RIGHT_IN1,
    BACK_RIGHT_IN2,
    BACK_RIGHT_PWM,
    br,
    INVERT_BACK_RIGHT
  );
}

void driveWheelPowers(int fl, int fr, int bl, int br) {
  setMotorPower(
    FRONT_LEFT_IN1,
    FRONT_LEFT_IN2,
    FRONT_LEFT_PWM,
    fl,
    INVERT_FRONT_LEFT
  );
  setMotorPower(
    FRONT_RIGHT_IN1,
    FRONT_RIGHT_IN2,
    FRONT_RIGHT_PWM,
    fr,
    INVERT_FRONT_RIGHT
  );
  setMotorPower(
    BACK_LEFT_IN1,
    BACK_LEFT_IN2,
    BACK_LEFT_PWM,
    bl,
    INVERT_BACK_LEFT
  );
  setMotorPower(
    BACK_RIGHT_IN1,
    BACK_RIGHT_IN2,
    BACK_RIGHT_PWM,
    br,
    INVERT_BACK_RIGHT
  );
}

void applyTwistCommand(int forward, int left, int counterClockwise) {
  forward = constrain(forward, -1000, 1000);
  left = constrain(left, -1000, 1000);
  counterClockwise = constrain(counterClockwise, -1000, 1000);

  const int deadband = 35;
  if (abs(forward) < deadband) forward = 0;
  if (abs(left) < deadband) left = 0;
  if (abs(counterClockwise) < deadband) counterClockwise = 0;

  if (emergencyStopLatched) {
    serialPrintln("SAFETY: Emergency stop latched - TWIST blocked");
    stopAll();
    return;
  }
  if (forward == 0 && left == 0 && counterClockwise == 0) {
    stopAll();
    return;
  }
  if (frontCliff && backCliff) {
    serialPrintln("SAFETY: Both directions blocked");
    stopAll();
    return;
  }

  // At an edge, permit only a pure command directly away from it.
  if (
    frontCliff &&
    (forward >= 0 || left != 0 || counterClockwise != 0)
  ) {
    serialPrintln("SAFETY: Front edge - TWIST blocked");
    stopAll();
    return;
  }
  if (
    backCliff &&
    (forward <= 0 || left != 0 || counterClockwise != 0)
  ) {
    serialPrintln("SAFETY: Back edge - TWIST blocked");
    stopAll();
    return;
  }

  // Standard X-layout inverse kinematics matching the verified discrete
  // motion patterns below.
  long fl = 0;
  long fr = 0;
  long bl = 0;
  long br = 0;
  if (!OPPOSITE_ROLLER_LAYOUT) {
    fl = (long)forward - left - counterClockwise;
    fr = (long)forward + left + counterClockwise;
    bl = (long)forward + left - counterClockwise;
    br = (long)forward - left + counterClockwise;
  } else {
    fl = (long)forward + left - counterClockwise;
    fr = (long)forward - left + counterClockwise;
    bl = (long)forward + left - counterClockwise;
    br = (long)forward - left + counterClockwise;
  }
  long peak = max(max(abs(fl), abs(fr)), max(abs(bl), abs(br)));
  if (peak > 1000) {
    fl = fl * 1000 / peak;
    fr = fr * 1000 / peak;
    bl = bl * 1000 / peak;
    br = br * 1000 / peak;
  }

  twistForward = forward;
  twistLeft = left;
  twistCounterClockwise = counterClockwise;
  currentMotion = VELOCITY_CONTROL;
  driveWheelPowers(
    (int)(fl * motorSpeed / 1000),
    (int)(fr * motorSpeed / 1000),
    (int)(bl * motorSpeed / 1000),
    (int)(br * motorSpeed / 1000)
  );
  motorsRunning = true;
  lastCommandTime = millis();
}

// ============================================================
// MECANUM MOVEMENTS
//
// Wheel order:
// FRONT LEFT, FRONT RIGHT, BACK LEFT, BACK RIGHT
// ============================================================
void applyMotion(Motion motion) {
  // A latched emergency stop can only be cleared by RESET_ESTOP.
  // Always allow stopping and status/telemetry processing.
  if (emergencyStopLatched && motion != STOPPED) {
    serialPrintln("SAFETY: Emergency stop latched - command blocked");
    stopAll();
    return;
  }

    // Always allow stopping.
  if (motion != STOPPED) {

    // Both directions unsafe: Cubey must remain stopped.
    if (frontCliff && backCliff) {
      serialPrintln("SAFETY: Both directions blocked");
      stopAll();
      return;
    }

    // Front edge: only permit movement backward.
    if (
      frontCliff &&
      !movesTowardBack(motion)
    ) {
      serialPrintln("SAFETY: Front edge - command blocked");
      stopAll();
      return;
    }

    // Back edge: only permit movement forward.
    if (
      backCliff &&
      !movesTowardFront(motion)
    ) {
      serialPrintln("SAFETY: Back edge - command blocked");
      stopAll();
      return;
    }
  }

  currentMotion = motion;

  int fl = 0;
  int fr = 0;
  int bl = 0;
  int br = 0;

  if (!OPPOSITE_ROLLER_LAYOUT) {
    // Standard X roller layout.
    switch (motion) {
      case FORWARD:
        fl = 1;  fr = 1;  bl = 1;  br = 1;
        break;

      case BACKWARD:
        fl = -1; fr = -1; bl = -1; br = -1;
        break;

      case STRAFE_LEFT:
        fl = -1; fr = 1;  bl = 1;  br = -1;
        break;

      case STRAFE_RIGHT:
        fl = 1;  fr = -1; bl = -1; br = 1;
        break;

      case ROTATE_LEFT:
        fl = -1; fr = 1;  bl = -1; br = 1;
        break;

      case ROTATE_RIGHT:
        fl = 1;  fr = -1; bl = 1;  br = -1;
        break;

      case FORWARD_LEFT:
        fl = 0;  fr = 1;  bl = 1;  br = 0;
        break;

      case FORWARD_RIGHT:
        fl = 1;  fr = 0;  bl = 0;  br = 1;
        break;

      case BACKWARD_LEFT:
        fl = -1; fr = 0;  bl = 0;  br = -1;
        break;

      case BACKWARD_RIGHT:
        fl = 0;  fr = -1; bl = -1; br = 0;
        break;

      case STOPPED:
      default:
        break;
    }
  }
  else {
    // Opposite roller layout.
    switch (motion) {
      case FORWARD:
        fl = 1;  fr = 1;  bl = 1;  br = 1;
        break;

      case BACKWARD:
        fl = -1; fr = -1; bl = -1; br = -1;
        break;

      case STRAFE_LEFT:
        fl = 1;  fr = -1; bl = 1;  br = -1;
        break;

      case STRAFE_RIGHT:
        fl = -1; fr = 1;  bl = -1; br = 1;
        break;

      case ROTATE_LEFT:
        fl = -1; fr = 1;  bl = -1; br = 1;
        break;

      case ROTATE_RIGHT:
        fl = 1;  fr = -1; bl = 1;  br = -1;
        break;

      case FORWARD_LEFT:
        fl = 1;  fr = 0;  bl = 1;  br = 0;
        break;

      case FORWARD_RIGHT:
        fl = 0;  fr = 1;  bl = 0;  br = 1;
        break;

      case BACKWARD_LEFT:
        fl = -1; fr = 0;  bl = -1; br = 0;
        break;

      case BACKWARD_RIGHT:
        fl = 0;  fr = -1; bl = 0;  br = -1;
        break;

      case STOPPED:
      default:
        break;
    }
  }

  driveWheels(fl, fr, bl, br);

  if (motion == STOPPED) {
    motorsRunning = false;
  }
  else {
    motorsRunning = true;
    lastCommandTime = millis();
  }

  printMotion(motion);
}

void stopAll() {
  currentMotion = STOPPED;
  twistForward = 0;
  twistLeft = 0;
  twistCounterClockwise = 0;
  driveWheels(0, 0, 0, 0);
  motorsRunning = false;
}

// ============================================================
// SERIAL LOGGING
// ============================================================
void printMotion(Motion motion) {
  serialPrint("Motion: ");

  switch (motion) {
    case FORWARD:
      serialPrintln("FORWARD");
      break;
    case BACKWARD:
      serialPrintln("BACKWARD");
      break;
    case STRAFE_LEFT:
      serialPrintln("STRAFE LEFT");
      break;
    case STRAFE_RIGHT:
      serialPrintln("STRAFE RIGHT");
      break;
    case ROTATE_LEFT:
      serialPrintln("ROTATE LEFT");
      break;
    case ROTATE_RIGHT:
      serialPrintln("ROTATE RIGHT");
      break;
    case FORWARD_LEFT:
      serialPrintln("FORWARD LEFT");
      break;
    case FORWARD_RIGHT:
      serialPrintln("FORWARD RIGHT");
      break;
    case BACKWARD_LEFT:
      serialPrintln("BACKWARD LEFT");
      break;
    case BACKWARD_RIGHT:
      serialPrintln("BACKWARD RIGHT");
      break;
    case STOPPED:
    default:
      serialPrintln("STOP");
      break;
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
// MOVEMENT DIRECTION HELPERS
// ============================================================
bool movesTowardFront(Motion motion) {
  if (motion == VELOCITY_CONTROL) {
    return twistForward > 35;
  }
  return (
    motion == FORWARD ||
    motion == FORWARD_LEFT ||
    motion == FORWARD_RIGHT
  );
}

bool movesTowardBack(Motion motion) {
  if (motion == VELOCITY_CONTROL) {
    return twistForward < -35;
  }
  return (
    motion == BACKWARD ||
    motion == BACKWARD_LEFT ||
    motion == BACKWARD_RIGHT
  );
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
