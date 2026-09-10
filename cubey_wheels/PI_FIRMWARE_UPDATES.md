# Pi firmware updates after the OTA bootstrap upload

This firmware provides an authenticated HTTP updater at the ESP's access-point
address, normally `192.168.4.1`. It is installed only after this version is
uploaded once through the usual USB programming workflow.

The Pi can switch itself to `Cubey-Control` for the update, then restore its
normal Wi-Fi connection and remove the temporary Wi-Fi profile. This briefly
interrupts Gemini Live and other Internet-dependent services. From the repository
root on the Pi, run:

```bash
sudo bash scripts/firmware/install_arduino_toolchain.sh   # once per Pi
export CUBEY_ESP_PASSWORD='your Cubey-Control password'
sudo -E python3 scripts/firmware/update_esp_via_cubey_wifi.py --compile
```

The one-time install places a pinned `arduino-cli`, the `esp32:esp32` core, and
the Adafruit sensor libraries under `/opt/cubey-arduino`, then proves they build
`cubey_wheels`. Pinning matters: the sketch subclasses `Adafruit_BNO08x` and
touches its HAL internals, which are not stable across library releases. Re-run
the installer to change `ARDUINO_CLI_VERSION`, `ESP32_CORE_VERSION`, or the
library pins at the top of the script.

The updater authenticates with user `cubey` and the Cubey-Control password. It
stops motors before writing, asks the ESP update library to verify the image, and
restarts only after returning a successful HTTP response. A failed or interrupted
transfer leaves the currently running firmware intact. The script first queries
`/firmware/status`, so it clearly reports when the one-time bootstrap firmware
has not been installed yet.

Without the toolchain, build the `.bin` elsewhere and upload only that reviewed
artifact:

```bash
sudo -E python3 scripts/firmware/update_esp_via_cubey_wifi.py --firmware /path/to/cubey_wheels.ino.bin
```

For an explicit connection name or a nonstandard ESP password, use
`--wifi-password` and `--esp-password`. The script compiles before switching
networks, restores the exact Wi-Fi profile active at launch in a `finally` block,
and removes only the `Cubey ESP temporary update` profile it created.
