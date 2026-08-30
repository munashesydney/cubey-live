#include "status_led.h"
#include "../config/config.h"

// ============================================================
// STATUS RGB LED (SOFT / DIM ORANGE)
// ============================================================
void setStatusLed(uint8_t r, uint8_t g, uint8_t b) {
  // Several ESP32-S3 boards place the RGB LED on GPIO48, which is also
  // Cubey's front sensor XSHUT pin. Never drive the LED on that hardware.
  #if defined(ESP32) && RGB_BUILTIN != FRONT_XSHUT
  neopixelWrite(RGB_BUILTIN, r, g, b);
  #endif
}
