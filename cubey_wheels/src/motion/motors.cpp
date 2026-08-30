#include "motors.h"
#include "../config/config.h"
#include "../comm/serial_comm.h"

// State definitions
Motion currentMotion = STOPPED;
int motorSpeed = 180;
int twistForward = 0;
int twistLeft = 0;
int twistCounterClockwise = 0;
bool motorsRunning = false;
bool emergencyStopLatched = false;
unsigned long lastCommandTime = 0;

// ============================================================
// GPIO SETUP
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
