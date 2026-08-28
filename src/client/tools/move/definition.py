"""Definition of the move tool."""

from src.client.tools.registry import build_gemini_tool

MOVE_TOOL_DECLARATION = build_gemini_tool("move")
MOVE_FUNCTION_DECLARATION = MOVE_TOOL_DECLARATION.function_declarations[0]
