"""Current local time tool."""

from .definition import CURRENT_TIME_FUNCTION_DECLARATION, CURRENT_TIME_TOOL_DECLARATION
from .execute import execute_current_time_tool

__all__ = [
    "CURRENT_TIME_FUNCTION_DECLARATION",
    "CURRENT_TIME_TOOL_DECLARATION",
    "execute_current_time_tool",
]
