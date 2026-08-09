"""
Definition of the 'memories' tool — schema is owned by the tool registry.
"""

from src.client.tools.registry import build_gemini_tool

MEMORIES_TOOL_DECLARATION = build_gemini_tool("memories")
MEMORIES_FUNCTION_DECLARATION = MEMORIES_TOOL_DECLARATION.function_declarations[0]
