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


def list_camera_devices(max_devices: int = 4) -> list[dict[str, Any]]:
    """
    Enumerate available camera devices on the host system.
    Detects native Raspberry Pi Picamera2 CSI sensors and USB webcams.
    Returns a list of dictionaries with 'index', 'name', and 'backend'.
    """
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
        for index in range(max_devices):
            if any(d["index"] == index and d.get("backend") == "picamera2" for d in available):
                continue
            try:
                cap = cv2.VideoCapture(index)
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
                    cap.release()
            except Exception as e:
                logger.debug("Probed camera index %d (not available): %s", index, e)

    return available
