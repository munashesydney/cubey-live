#!/usr/bin/env python3
"""Temporarily join Cubey-Control, install ESP firmware, then restore Pi Wi-Fi."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error

from update_esp_firmware import compile_firmware, multipart_image, request


TEMP_CONNECTION = "Cubey ESP temporary update"


def nmcli(*args: str, check: bool = True) -> str:
    completed = subprocess.run(["nmcli", *args], check=check, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def active_wifi_connection() -> str:
    for line in nmcli("-t", "-f", "NAME,TYPE", "connection", "show", "--active").splitlines():
        if line.endswith(":802-11-wireless"):
            return line.rsplit(":", 1)[0]
    raise RuntimeError("Pi has no active Wi-Fi connection to restore")


def wait_for_ssid(ssid: str, wifi_device: str, timeout_s: float = 20.0) -> None:
    """Wait until the ESP access point is actually visible to the Pi."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        visible = nmcli("-t", "-f", "SSID", "device", "wifi", "list",
                        "ifname", wifi_device, "--rescan", "yes", check=False)
        if ssid in visible.splitlines():
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"{ssid!r} is not visible from {wifi_device}; the ESP access point may be off or out of range"
    )


def wait_for_updater(host: str, password: str, timeout_s: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _, data = request(f"http://{host}/firmware/status", password)
            return json.loads(data)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as error:
            last_error = error
            time.sleep(1.0)
    raise RuntimeError(f"ESP updater did not respond at {host}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wifi-password", default=os.getenv("CUBEY_ESP_PASSWORD", ""))
    parser.add_argument("--esp-password", default=os.getenv("CUBEY_ESP_PASSWORD", ""))
    parser.add_argument("--ssid", default="Cubey-Control")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--wifi-device", default="wlan0")
    parser.add_argument("--firmware", type=Path)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--arduino-cli", default="arduino-cli")
    parser.add_argument("--fqbn", default="esp32:esp32:esp32s3")
    parser.add_argument("--keep-temporary-connection", action="store_true")
    args = parser.parse_args()
    if not args.wifi_password or not args.esp_password:
        parser.error("--wifi-password and --esp-password are required (or set CUBEY_ESP_PASSWORD)")
    if args.compile == (args.firmware is not None):
        parser.error("choose exactly one of --compile or --firmware")

    # Compile before disconnecting Internet-dependent Pi services.
    image = compile_firmware(args.arduino_cli, args.fqbn) if args.compile else args.firmware
    if not image or not image.is_file():
        parser.error(f"firmware image does not exist: {image}")
    original_connection = active_wifi_connection()
    print(f"Saving Pi Wi-Fi connection: {original_connection}")
    connected_to_cubey = False
    try:
        # A named, non-autoconnecting profile is easy to remove after use.
        nmcli("connection", "delete", TEMP_CONNECTION, check=False)
        wait_for_ssid(args.ssid, args.wifi_device)
        nmcli("device", "wifi", "connect", args.ssid, "password", args.wifi_password,
              "ifname", args.wifi_device, "name", TEMP_CONNECTION)
        nmcli("connection", "modify", TEMP_CONNECTION, "connection.autoconnect", "no",
              "ipv4.never-default", "yes")
        connected_to_cubey = True
        status = wait_for_updater(args.host, args.esp_password)
        print("ESP updater:", json.dumps(status, sort_keys=True))
        body, content_type = multipart_image(image)
        _, reply = request(f"http://{args.host}/firmware", args.esp_password, body, content_type)
        result = json.loads(reply)
        if not result.get("ok"):
            raise RuntimeError(f"ESP rejected firmware: {reply}")
        print("ESP accepted and verified firmware; it is restarting.")
        return 0
    finally:
        if connected_to_cubey:
            print(f"Restoring Pi Wi-Fi connection: {original_connection}")
            try:
                nmcli("connection", "up", "id", original_connection, "ifname", args.wifi_device)
            finally:
                if not args.keep_temporary_connection:
                    nmcli("connection", "delete", TEMP_CONNECTION, check=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"Firmware update failed: {error}", file=sys.stderr)
        raise SystemExit(1)
