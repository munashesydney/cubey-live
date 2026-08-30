#pragma once

#include <Arduino.h>
#include <Adafruit_VL53L0X.h>

// ============================================================
// CLIFF & EDGE DETECTION SENSORS (DUAL VL53L0X)
// ============================================================

void setupCliffSensors();
bool readFloorSensor(Adafruit_VL53L0X &sensor, uint16_t &distance);
void updateCliffSafety();
void performSafetyEscape(bool moveForward);
