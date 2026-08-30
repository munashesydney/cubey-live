#pragma once

#include <Arduino.h>

// ============================================================
// SERIAL PROTOCOL, TELEMETRY & MULTI-STREAM LOGGING
// ============================================================

void serialPrint(const String &s);
void serialPrintln(const String &s);
void serialPrintln();
void reportChargingDiagnostic(
  float batteryVoltage,
  float baselineVoltage,
  float deltaVoltage,
  const char* reason
);
void sendTelemetry(bool broadcast);
void handleIncomingLine(String line);
void processSerialCommands();
void handleCommandName(String command);
void testSingleMotor(String motorName, int direction, int speed = 0);
