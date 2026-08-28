"""
Execution logic for the 'camera' tool call.
Controls camera vision streaming with an automatic 30-second auto-off timer.
"""

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Module-level timer state for camera auto-off
_timer_lock = threading.Lock()
_active_camera_timer: Optional[threading.Timer] = None


def stop_active_camera_timer() -> None:
    """Cancel any active camera auto-off countdown timer."""
    global _active_camera_timer
    with _timer_lock:
        if _active_camera_timer is not None:
            try:
                _active_camera_timer.cancel()
            except Exception as e:
                logger.debug("Failed cancelling camera timer: %s", e)
            _active_camera_timer = None


def _on_camera_timer_expired(
    on_toggle_camera: Optional[Callable[[Optional[bool]], bool]],
    camera_service: Optional[Any],
    live_client: Optional[Any],
) -> None:
    """Invoked when the 30-second camera timer expires to automatically turn off camera."""
    global _active_camera_timer
    with _timer_lock:
        _active_camera_timer = None

    logger.info("⏱️ [Camera Timer Expired]: 30 seconds elapsed. Automatically turning off camera feed.")

    try:
        if on_toggle_camera:
            on_toggle_camera(False)
        else:
            if live_client and hasattr(live_client, "set_camera_streaming"):
                live_client.set_camera_streaming(False)
            if camera_service and hasattr(camera_service, "stop"):
                camera_service.stop()
    except Exception as e:
        logger.warning("Error turning off camera on timer expiry: %s", e)


def execute_camera_tool(
    action: str = "turn_on",
    duration_seconds: Optional[int] = None,
    on_toggle_camera: Optional[Callable[[Optional[bool]], bool]] = None,
    camera_service: Optional[Any] = None,
    live_client: Optional[Any] = None,
) -> dict[str, Any]:
    """
    Executes the 'camera' tool call received from Gemini Live API.
    Turns camera streaming on with an automatic 30-second auto-off timer.
    """
    global _active_camera_timer

    clean_action = str(action or "turn_on").lower().strip()
    if clean_action in {"on", "start", "enable"}:
        clean_action = "turn_on"
    elif clean_action in {"off", "stop", "disable"}:
        clean_action = "turn_off"
    elif clean_action not in {"turn_on", "turn_off", "status"}:
        clean_action = "turn_on"

    logger.info("📷 AI Tool Call Executed: camera(action='%s')", clean_action)

    if clean_action == "turn_on":
        # 1. Activate hardware capture and live streaming
        if on_toggle_camera:
            on_toggle_camera(True)
        else:
            if camera_service and hasattr(camera_service, "start"):
                camera_service.start()
            if live_client and hasattr(live_client, "set_camera_streaming"):
                live_client.set_camera_streaming(True)

        # 2. Configure and start 30-second auto-off timer
        timeout = min(30, max(1, int(duration_seconds or 30)))
        stop_active_camera_timer()

        with _timer_lock:
            _active_camera_timer = threading.Timer(
                timeout,
                _on_camera_timer_expired,
                args=(on_toggle_camera, camera_service, live_client),
            )
            _active_camera_timer.daemon = True
            _active_camera_timer.start()

        return {
            "status": "camera_activated",
            "action": "turn_on",
            "camera_active": True,
            "duration_seconds": timeout,
            "message": (
                f"Camera is now active and streaming visual feed. "
                f"The feed will automatically turn off in {timeout} seconds. "
                f"If you need visual input again after it shuts off, call the camera tool again to re-enable it."
            ),
        }

    elif clean_action == "turn_off":
        stop_active_camera_timer()

        if on_toggle_camera:
            on_toggle_camera(False)
        else:
            if live_client and hasattr(live_client, "set_camera_streaming"):
                live_client.set_camera_streaming(False)
            if camera_service and hasattr(camera_service, "stop"):
                camera_service.stop()

        return {
            "status": "camera_deactivated",
            "action": "turn_off",
            "camera_active": False,
            "message": "Camera feed has been turned off.",
        }

    else:  # status
        is_active = False
        if live_client and hasattr(live_client, "is_camera_streaming"):
            is_active = live_client.is_camera_streaming
        elif camera_service and hasattr(camera_service, "is_running"):
            is_active = camera_service.is_running

        return {
            "status": "camera_status",
            "action": "status",
            "camera_active": is_active,
            "message": f"Camera streaming is currently {'active' if is_active else 'off'}.",
        }
