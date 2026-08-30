#pragma once

#include <Arduino.h>
#include "../config/config.h"

// ============================================================
// MOTOR CONTROL & MECANUM INVERSE KINEMATICS
// ============================================================

void setupPins();
void setMotor(int in1, int in2, int pwmPin, int logicalDirection, bool inverted);
void setMotorPower(int in1, int in2, int pwmPin, int signedPower, bool inverted);
void driveWheels(int fl, int fr, int bl, int br);
void driveWheelPowers(int fl, int fr, int bl, int br);
void applyTwistCommand(int forward, int left, int counterClockwise);
void applyMotion(Motion motion);
void stopAll();
const char* motionToString(Motion motion);
void printMotion(Motion motion);
bool movesTowardFront(Motion motion);
bool movesTowardBack(Motion motion);
