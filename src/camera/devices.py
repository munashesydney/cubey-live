"""
Camera device discovery and enumeration utilities.
"""

import logging
from typing import Any

try:
    import cv2
except ImportError:
    cv2 = None

logger = logging.getLogger(__name__)


def list_camera_devices(max_devices: int = 4) -> list[dict[str, Any]]:
    """
    Enumerate available camera devices on the host system.
    Returns a list of dictionaries with 'index' and 'name'.
    """
    if cv2 is None:
        logger.warning("OpenCV is not available; cannot enumerate camera devices.")
        return []

    available: list[dict[str, Any]] = []

    for index in range(max_devices):
        try:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
                    name = f"Camera {index} ({width}x{height})"
                    available.append({"index": index, "name": name})
                cap.release()
        except Exception as e:
            logger.debug("Probed camera index %d (not available): %s", index, e)

    return available
