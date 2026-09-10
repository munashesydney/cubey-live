# Pi firmware updates after the OTA bootstrap upload

This firmware provides an authenticated HTTP updater at the ESP's access-point
address, normally `192.168.4.1`. It is installed only after this version is
uploaded once through the usual USB programming workflow.

The Pi can switch itself to `Cubey-Control` for the update, then restore its
normal Wi-Fi connection and remove the temporary Wi-Fi profile. This briefly
interrupts Gemini Live and other Internet-dependent services. From the repository
root on the Pi, run:

```bash
export CUBEY_ESP_PASSWORD='your Cubey-Control password'
sudo -E python3 scripts/firmware/update_esp_via_cubey_wifi.py --compile
```

The Pi needs `arduino-cli`, the ESP32 core, and the firmware's Arduino libraries
for `--compile`. Or build the `.bin` elsewhere and upload only that reviewed
artifact:

```bash
sudo -E python3 scripts/firmware/update_esp_via_cubey_wifi.py --firmware /path/to/cubey_wheels.ino.bin
```

The updater authenticates with user `cubey` and the Cubey-Control password. It
stops motors before writing, asks the ESP update library to verify the image, and
restarts only after returning a successful HTTP response. A failed or interrupted
transfer leaves the currently running firmware intact. The script first queries
`/firmware/status`, so it clearly reports when the one-time bootstrap firmware
has not been installed yet.

For an explicit connection name or a nonstandard ESP password, use
`--wifi-password` and `--esp-password`. The script compiles before switching
networks, restores the exact Wi-Fi profile active at launch in a `finally` block,
and removes only the `Cubey ESP temporary update` profile it created.
