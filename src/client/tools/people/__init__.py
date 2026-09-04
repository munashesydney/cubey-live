"""People and face-enrollment tools for Gemini Live."""

from src.client.tools.people.definition import (
    ADD_FACE_FUNCTION_DECLARATION,
    ADD_FACE_TOOL_DECLARATION,
)
from src.client.tools.people.execute import execute_add_face_tool

__all__ = [
    "ADD_FACE_FUNCTION_DECLARATION",
    "ADD_FACE_TOOL_DECLARATION",
    "execute_add_face_tool",
]
