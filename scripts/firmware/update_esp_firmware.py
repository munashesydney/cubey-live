#!/usr/bin/env python3
"""Compile Cubey Wheels, then install it through the ESP's authenticated Wi-Fi updater."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[2]
SKETCH_DIR = REPO_ROOT / "cubey_wheels"

# Toolchain installed by scripts/firmware/install_arduino_toolchain.sh. Pinned
# paths make --compile work under sudo, where PATH and HOME are not the user's.
TOOLCHAIN_BIN = Path("/opt/cubey-arduino/bin/arduino-cli")
TOOLCHAIN_CONFIG = Path("/opt/cubey-arduino/arduino-cli.yaml")


def resolve_arduino_cli(explicit: str | None = None) -> str:
    """Prefer the Pi's pinned toolchain, then PATH, so sudo cannot hide it."""
    if explicit:
        return explicit
    override = os.getenv("CUBEY_ARDUINO_CLI")
    if override:
        return override
    if TOOLCHAIN_BIN.is_file():
        return str(TOOLCHAIN_BIN)
    return "arduino-cli"


def resolve_arduino_config(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    override = os.getenv("CUBEY_ARDUINO_CONFIG")
    if override:
        return override
    if TOOLCHAIN_CONFIG.is_file():
        return str(TOOLCHAIN_CONFIG)
    return None


def request(url: str, password: str, body: bytes | None = None, content_type: str | None = None):
    headers = {"Authorization": "Basic " + base64.b64encode(f"cubey:{password}".encode()).decode()}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def compile_firmware(arduino_cli: str, fqbn: str, config_file: str | None = None) -> Path:
    # Keep every generated file out of the checkout: arduino-cli would otherwise
    # default to <sketch>/build and dirty the repository.
    build_dir = Path(tempfile.mkdtemp(prefix="cubey-esp-build-"))
    output_dir = Path(tempfile.mkdtemp(prefix="cubey-esp-artifacts-"))
    command = [arduino_cli]
    if config_file:
        command += ["--config-file", config_file]
    command += [
        "compile",
        "--fqbn", fqbn,
        "--build-path", str(build_dir),
        "--output-dir", str(output_dir),
        str(SKETCH_DIR),
    ]
    subprocess.run(command, check=True)
    image = output_dir / "cubey_wheels.ino.bin"
    if not image.is_file():
        raise RuntimeError(f"Arduino CLI did not create {image}")
    return image


def multipart_image(image: Path) -> tuple[bytes, str]:
    boundary = "----CubeyFirmwareBoundary"
    payload = image.read_bytes()
    body = b"\r\n".join((
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="firmware"; filename="{image.name}"'.encode(),
        b"Content-Type: application/octet-stream",
        b"",
        payload,
        f"--{boundary}--".encode(),
        b"",
    ))
    return body, f"multipart/form-data; boundary={boundary}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("CUBEY_ESP_HOST", "192.168.4.1"))
    parser.add_argument("--password", default=os.getenv("CUBEY_ESP_PASSWORD", ""),
                        help="Cubey-Control password, or set CUBEY_ESP_PASSWORD")
    parser.add_argument("--firmware", type=Path, help="Existing .bin image to install")
    parser.add_argument("--compile", action="store_true", help="Build cubey_wheels before upload")
    parser.add_argument("--arduino-cli", default=None, help="Path to arduino-cli (default: the Pi toolchain, then PATH)")
    parser.add_argument("--arduino-config", default=None, help="arduino-cli config file (default: the Pi toolchain config)")
    parser.add_argument("--fqbn", default="esp32:esp32:esp32s3")
    args = parser.parse_args()
    if not args.password:
        parser.error("--password or CUBEY_ESP_PASSWORD is required")
    if args.compile == (args.firmware is not None):
        parser.error("choose exactly one of --compile or --firmware")
    image = compile_firmware(resolve_arduino_cli(args.arduino_cli), args.fqbn,
                             resolve_arduino_config(args.arduino_config)) if args.compile else args.firmware
    if not image.is_file():
        parser.error(f"firmware image does not exist: {image}")
    root = f"http://{args.host}"
    try:
        _, status = request(root + "/firmware/status", args.password)
        print("ESP status:", status)
        body, content_type = multipart_image(image)
        _, result = request(root + "/firmware", args.password, body, content_type)
        response = json.loads(result)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        print(f"Firmware update failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
