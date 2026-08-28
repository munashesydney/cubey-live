"""
Definition of the 'camera' tool — schema is owned by the tool registry.
"""

from src.client.tools.registry import build_gemini_tool

CAMERA_TOOL_DECLARATION = build_gemini_tool("camera")
CAMERA_FUNCTION_DECLARATION = CAMERA_TOOL_DECLARATION.function_declarations[0]
