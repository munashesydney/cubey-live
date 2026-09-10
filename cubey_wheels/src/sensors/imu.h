#pragma once

#include <Arduino.h>

// ============================================================
// BNO08X 9-DOF IMU SENSOR INTERFACE
// ============================================================

void setupIMU();
void updateIMU();
void sendIMUSnapshot(bool usb = false);

// True while the BNO08x is attached and reports have been requested. A boot
// probe failure is recoverable, so this can become true after startup.
bool imuConnected();
