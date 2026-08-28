"""
Executor for Cubey's Move tool.

Controls the 4-wheel mecanum drive base via WheelsService.
Supports forward, backward, rotation, diagonals, and stop.
Explicitly disables sideways strafing.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Mapping from natural/agent action names to exact ESP32 firmware commands
MOTION_MAP = {
    "forward": "forward",
    "fwd": "forward",
    "front": "forward",
    "backward": "backward",
    "back": "backward",
    "reverse": "backward",
    "rotate_left": "rotateLeft",
    "rotateleft": "rotateLeft",
    "turn_left": "rotateLeft",
    "turnleft": "rotateLeft",
    "spin_left": "rotateLeft",
    "spinleft": "rotateLeft",
    "left": "rotateLeft",
    "rotate_right": "rotateRight",
    "rotateright": "rotateRight",
    "turn_right": "rotateRight",
    "turnright": "rotateRight",
    "spin_right": "rotateRight",
    "spinright": "rotateRight",
    "right": "rotateRight",
    "forward_left": "forwardLeft",
    "forwardleft": "forwardLeft",
    "front_left": "forwardLeft",
    "forward_right": "forwardRight",
    "forwardright": "forwardRight",
    "front_right": "forwardRight",
    "backward_left": "backwardLeft",
    "backwardleft": "backwardLeft",
    "back_left": "backwardLeft",
    "backward_right": "backwardRight",
    "backwardright": "backwardRight",
    "back_right": "backwardRight",
    "stop": "stop",
    "halt": "stop",
    "brake": "stop",
}

DISABLED_STRAFE_ACTIONS = {
    "strafe_left",
    "strafeleft",
    "strafe_right",
    "straferight",
    "sideways_left",
    "sideways_right",
    "straddle_left",
    "straddle_right",
}


def execute_move_tool(
    action: str,
    duration_seconds: float = 1.0,
    speed: Optional[int] = None,
    wheels_service: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Executes a verified physical wheel movement on Cubey.
    """
    norm_action = str(action or "").strip().lower()

    # 1. Check for explicitly disabled strafing actions
    if norm_action in DISABLED_STRAFE_ACTIONS:
        return {
            "status": "disabled_action",
            "action": action,
            "message": (
                "Sideways strafing is disabled on Cubey. Please use "
                "'forward', 'backward', 'rotate_left', 'rotate_right', "
                "or diagonal movements ('forward_left', 'forward_right') instead."
            ),
        }

    # 2. Validate action exists in motion map
    if norm_action not in MOTION_MAP:
        return {
            "status": "invalid_action",
            "action": action,
            "message": (
                f"Unknown movement action '{action}'. Allowed: forward, "
                "backward, rotate_left, rotate_right, forward_left, "
                "forward_right, backward_left, backward_right, stop."
            ),
        }

    cmd = MOTION_MAP[norm_action]

    # 3. Get or initialize WheelsService
    if wheels_service is None:
        from src.services.wheels_service import get_wheels_service
        wheels_service = get_wheels_service()

    if not wheels_service.is_connected:
        wheels_service.connect()

    # 4. Set speed if specified
    if speed is not None:
        wheels_service.set_speed(speed)

    # 5. Execute motion
    if cmd == "stop":
        wheels_service.stop()
        return {
            "status": "success",
            "action": "stop",
            "message": "Cubey stopped all wheel movement.",
            "speed": wheels_service.telemetry.speed,
            "battery_pct": wheels_service.telemetry.battery_pct,
        }

    # Clamp duration between 0.1 seconds and 10.0 seconds
    duration_s = max(0.1, min(float(duration_seconds or 1.0), 10.0))
    duration_ms = int(duration_s * 1000)

    # Dispatch pulse movement
    wheels_service.pulse(cmd, duration_ms=duration_ms)

    return {
        "status": "success",
        "action": norm_action,
        "duration_seconds": duration_s,
        "speed": wheels_service.telemetry.speed,
        "battery_pct": wheels_service.telemetry.battery_pct,
        "message": f"Cubey moved {norm_action} for {duration_s}s at speed {wheels_service.telemetry.speed}.",
    }
