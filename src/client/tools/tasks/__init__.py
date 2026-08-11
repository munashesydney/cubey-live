"""
Tasks (scheduled AI jobs) tool package.
"""

from .definition import TASKS_FUNCTION_DECLARATION, TASKS_TOOL_DECLARATION
from .execute import execute_tasks_tool

__all__ = [
    "TASKS_FUNCTION_DECLARATION",
    "TASKS_TOOL_DECLARATION",
    "execute_tasks_tool",
]
