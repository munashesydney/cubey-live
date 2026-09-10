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


def request(url: str, password: str, body: bytes | None = None, content_type: str | None = None):
    headers = {"Authorization": "Basic " + base64.b64encode(f"cubey:{password}".encode()).decode()}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def compile_firmware(arduino_cli: str, fqbn: str) -> Path:
    output_dir = Path(tempfile.mkdtemp(prefix="cubey-esp-build-"))
    subprocess.run([arduino_cli, "compile", "--fqbn", fqbn, "--output-dir", str(output_dir), str(SKETCH_DIR)], check=True)
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
    parser.add_argument("--arduino-cli", default="arduino-cli")
    parser.add_argument("--fqbn", default="esp32:esp32:esp32s3")
    args = parser.parse_args()
    if not args.password:
        parser.error("--password or CUBEY_ESP_PASSWORD is required")
    if args.compile == (args.firmware is not None):
        parser.error("choose exactly one of --compile or --firmware")
    image = compile_firmware(args.arduino_cli, args.fqbn) if args.compile else args.firmware
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
