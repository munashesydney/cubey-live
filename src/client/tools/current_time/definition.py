"""Definition of the current-time tool; its schema lives in the registry."""

from src.client.tools.registry import build_gemini_tool

CURRENT_TIME_TOOL_DECLARATION = build_gemini_tool("current_time")
CURRENT_TIME_FUNCTION_DECLARATION = CURRENT_TIME_TOOL_DECLARATION.function_declarations[0]
