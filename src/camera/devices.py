"""
Camera device discovery and enumeration utilities.
Supports detecting native Raspberry Pi libcamera sensors (IMX708, etc.) and USB webcams.
"""

import logging
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

logger = logging.getLogger(__name__)


import time

_CACHED_DEVICES: list[dict[str, Any]] = []
_LAST_PROBE_TIME: float = 0.0
_PROBE_CACHE_TTL: float = 15.0


def list_camera_devices(max_devices: int = 4, force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    Enumerate available camera devices on the host system.
    Detects native Raspberry Pi Picamera2 CSI sensors and USB webcams.
    Returns a list of dictionaries with 'index', 'name', and 'backend'.
    Caches results for 15s to prevent freezing the UI on repeated calls.
    """
    global _CACHED_DEVICES, _LAST_PROBE_TIME
    now = time.time()
    if not force_refresh and _CACHED_DEVICES and (now - _LAST_PROBE_TIME < _PROBE_CACHE_TTL):
        return list(_CACHED_DEVICES)

    available: list[dict[str, Any]] = []

    # 1. Probe native Raspberry Pi Camera Module (CSI via libcamera / Picamera2)
    if Picamera2 is not None:
        try:
            # Check for cameras via Picamera2
            picam = Picamera2(0)
            model = "CSI Camera"
            try:
                cam_props = picam.camera_properties
                model = cam_props.get("Model", "Pi Camera Module")
            except Exception:
                pass
            available.append({
                "index": 0,
                "name": f"Pi Camera 0 ({model})",
                "backend": "picamera2",
            })
            picam.close()
        except Exception as e:
            logger.debug("Picamera2 device 0 probe: %s", e)

    # 2. Probe standard USB webcams via OpenCV
    if cv2 is not None:
        consecutive_failures = 0
        for index in range(max_devices):
            if any(d["index"] == index and d.get("backend") == "picamera2" for d in available):
                continue
            try:
                # Use CAP_DSHOW on Windows for fast device probing without MSMF timeouts
                backend_flag = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
                cap = cv2.VideoCapture(index, backend_flag)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
                        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
                        name = f"USB Camera {index} ({width}x{height})"
                        available.append({
                            "index": index,
                            "name": name,
                            "backend": "opencv",
                        })
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                    cap.release()
                else:
                    consecutive_failures += 1
            except Exception as e:
                logger.debug("Probed camera index %d (not available): %s", index, e)
                consecutive_failures += 1

            # Camera indices are sequential from 0; if index 0 or 1 fails, higher indices won't exist
            if consecutive_failures >= 1 and index >= 1:
                break

    _CACHED_DEVICES = list(available)
    _LAST_PROBE_TIME = now
    return available

