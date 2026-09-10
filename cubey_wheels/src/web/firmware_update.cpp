#include "firmware_update.h"

#include <Update.h>

#include "../comm/serial_comm.h"
#include "../config/config.h"
#include "../motion/motors.h"
#include "web_server.h"

namespace {
bool uploadAuthorized = false;
bool uploadSucceeded = false;
String uploadFailure;
unsigned long restartAt = 0;

bool authorizeFirmwareUpdate() {
  if (server.authenticate(FIRMWARE_UPDATE_USERNAME, FIRMWARE_UPDATE_PASSWORD)) return true;
  server.requestAuthentication();
  return false;
}

void sendUpdateStatus() {
  if (!authorizeFirmwareUpdate()) return;
  String response = "{\"version\":\"" + String(CUBEY_FIRMWARE_VERSION) +
                    "\",\"updating\":" + String(restartAt ? "true" : "false") +
                    ",\"motors_stopped\":" + String(motorsRunning ? "false" : "true") + "}";
  server.send(200, "application/json", response);
}

void receiveFirmwareChunk() {
  HTTPUpload &upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    uploadAuthorized = server.authenticate(FIRMWARE_UPDATE_USERNAME, FIRMWARE_UPDATE_PASSWORD);
    uploadSucceeded = false;
    uploadFailure = "";
    if (!uploadAuthorized) return;
    stopAll();
    if (!Update.begin(UPDATE_SIZE_UNKNOWN, U_FLASH)) {
      uploadFailure = "Could not begin flash update";
      Update.printError(Serial);
    }
    return;
  }
  if (!uploadAuthorized) return;
  if (upload.status == UPLOAD_FILE_WRITE) {
    if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
      uploadFailure = "Flash write failed";
      Update.printError(Serial);
    }
    return;
  }
  if (upload.status == UPLOAD_FILE_END) {
    uploadSucceeded = uploadFailure.length() == 0 && Update.end(true);
    if (!uploadSucceeded && uploadFailure.length() == 0) {
      uploadFailure = "Flash verification failed";
      Update.printError(Serial);
    }
    return;
  }
  if (upload.status == UPLOAD_FILE_ABORTED) {
    Update.abort();
    uploadFailure = "Upload aborted";
  }
}

void finishFirmwareUpload() {
  if (!authorizeFirmwareUpdate() || !uploadAuthorized) return;
  if (!uploadSucceeded) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"" + uploadFailure + "\"}");
    return;
  }
  stopAll();
  restartAt = millis() + 750;  // Allow the HTTP response to leave the ESP first.
  serialPrintln("FIRMWARE_UPDATE: verified image; restarting");
  server.send(200, "application/json", "{\"ok\":true,\"restarting\":true}");
}
}  // namespace

void setupFirmwareUpdateRoutes() {
  server.on("/firmware/status", HTTP_GET, sendUpdateStatus);
  server.on("/firmware", HTTP_POST, finishFirmwareUpload, receiveFirmwareChunk);
}

void handleFirmwareUpdateRestart() {
  if (restartAt && (long)(millis() - restartAt) >= 0) ESP.restart();
}
