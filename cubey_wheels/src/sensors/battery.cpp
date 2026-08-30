#include "battery.h"
#include "../config/config.h"
#include "../comm/serial_comm.h"

// State definitions
float lastRestingVoltage = 0.0f;
bool isCharging = false;
unsigned long lastTelemetryPush = 0;
unsigned long lastChargingDiagnosticTime = 0;

// ============================================================
// READ BATTERY VOLTAGE & DETECT CHARGING STATE
// ============================================================
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

// ============================================================
// 2S LI-ION DISCHARGE PERCENTAGE
// ============================================================
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
