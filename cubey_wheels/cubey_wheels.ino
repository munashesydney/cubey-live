#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>

#include "src/config/config.h"
#include "src/comm/status_led.h"
#include "src/comm/serial_comm.h"
#include "src/motion/motors.h"
#include "src/sensors/battery.h"
#include "src/sensors/cliff_sensors.h"
#include "src/web/webpage.h"
#include "src/web/web_server.h"

// ============================================================
// CUBEY — 4-WHEEL MECANUM CONTROLLER MAIN ENTRYPOINT
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
