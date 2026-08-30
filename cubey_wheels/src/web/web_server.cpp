#include "web_server.h"
#include "../config/config.h"
#include "../motion/motors.h"
#include "../comm/serial_comm.h"
#include "webpage.h"

// State definitions
WebServer server(80);

// ============================================================
// WEB ROUTES
// ============================================================
void setupWebServer() {
  server.on("/", HTTP_GET, []() {
    server.sendHeader(
      "Cache-Control",
      "no-store, no-cache, must-revalidate"
    );
    server.send_P(200, "text/html", webpage);
  });

  server.on("/command", HTTP_GET, []() {
    if (!server.hasArg("name")) {
      server.send(400, "text/plain", "Missing command");
      return;
    }

    String command = server.arg("name");
    handleCommandName(command);
    server.send(200, "text/plain", "OK");
  });

  server.on("/speed", HTTP_GET, []() {
    if (server.hasArg("value")) {
      motorSpeed = constrain(server.arg("value").toInt(), 0, 255);

      // Immediately reapply the current movement at the new speed.
      if (currentMotion != STOPPED) {
        applyMotion(currentMotion);
      }
    }

    server.send(200, "text/plain", String(motorSpeed));
  });

  server.onNotFound([]() {
    server.send(404, "text/plain", "Not found");
  });
}
