"""
Camera tool package for Gemini Live.
"""

from src.client.tools.camera.definition import (
    CAMERA_FUNCTION_DECLARATION,
    CAMERA_TOOL_DECLARATION,
)
from src.client.tools.camera.execute import (
    execute_camera_tool,
    stop_active_camera_timer,
)

__all__ = [
    "CAMERA_FUNCTION_DECLARATION",
    "CAMERA_TOOL_DECLARATION",
    "execute_camera_tool",
    "stop_active_camera_timer",
]
