"""
Definition of the 'messages' tool — schema is owned by the tool registry.
"""

from src.client.tools.registry import build_gemini_tool

MESSAGES_TOOL_DECLARATION = build_gemini_tool("messages")
MESSAGES_FUNCTION_DECLARATION = MESSAGES_TOOL_DECLARATION.function_declarations[0]
