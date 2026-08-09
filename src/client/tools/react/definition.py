"""
Definition of the 'react' tool — schema is owned by the tool registry.
"""

from src.client.tools.registry import build_gemini_tool

REACT_TOOL_DECLARATION = build_gemini_tool("react")
REACT_FUNCTION_DECLARATION = REACT_TOOL_DECLARATION.function_declarations[0]
