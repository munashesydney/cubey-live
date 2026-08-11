"""
AI Tools registry package for Gemini Live.

The tool *schemas* and the per-model tool *policy* live in `registry.py` —
that is the single source of truth. Executors live in the per-tool folders.
"""

from src.client.tools.memories import MEMORIES_TOOL_DECLARATION, execute_memories_tool
from src.client.tools.messages import MESSAGES_TOOL_DECLARATION, execute_messages_tool
from src.client.tools.react import REACT_TOOL_DECLARATION, execute_react_tool
from src.client.tools.tasks import TASKS_TOOL_DECLARATION, execute_tasks_tool
from src.client.tools.registry import (
    MODEL_TOOL_POLICY,
    TOOL_SCHEMAS,
    ToolContext,
    build_gemini_tool,
    build_gemini_tools,
    build_llama_tools,
    dispatch_tool_call,
    tool_names_for,
    tool_schemas_for,
)

__all__ = [
    "MEMORIES_TOOL_DECLARATION",
    "MESSAGES_TOOL_DECLARATION",
    "MODEL_TOOL_POLICY",
    "REACT_TOOL_DECLARATION",
    "TASKS_TOOL_DECLARATION",
    "TOOL_SCHEMAS",
    "ToolContext",
    "build_gemini_tool",
    "build_gemini_tools",
    "build_llama_tools",
    "dispatch_tool_call",
    "execute_memories_tool",
    "execute_messages_tool",
    "execute_react_tool",
    "execute_tasks_tool",
    "tool_names_for",
    "tool_schemas_for",
]
