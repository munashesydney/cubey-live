#include "serial_comm.h"
#include "../config/config.h"
#include "../motion/motors.h"
#include "../sensors/battery.h"
#include "../sensors/cliff_sensors.h"

// State definitions
unsigned long lastTelemetryTime = 0;
String serialRxBuffer = "";
String serial0RxBuffer = "";
String serial1RxBuffer = "";

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
