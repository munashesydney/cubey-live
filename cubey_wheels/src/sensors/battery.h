#pragma once

#include <Arduino.h>

// ============================================================
// BATTERY SENSING & CHARGING DETECTION
// ============================================================

float readBatteryVoltage();
int calculateBatteryPercent(float voltage);
