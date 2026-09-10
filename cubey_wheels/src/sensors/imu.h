#pragma once

#include <Arduino.h>

// ============================================================
// BNO08X 9-DOF IMU SENSOR INTERFACE
// ============================================================

void setupIMU();
void updateIMU();
void sendIMUSnapshot(bool usb = false);
