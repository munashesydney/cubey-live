"""
Definition of the 'tasks' tool — schema is owned by the tool registry.
"""

from src.client.tools.registry import build_gemini_tool

TASKS_TOOL_DECLARATION = build_gemini_tool("tasks")
TASKS_FUNCTION_DECLARATION = TASKS_TOOL_DECLARATION.function_declarations[0]
