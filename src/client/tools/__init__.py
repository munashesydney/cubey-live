"""
AI Tools registry package for Gemini Live.

The tool *schemas* and the per-model tool *policy* live in `registry.py` —
that is the single source of truth. Executors live in the per-tool folders.
"""

from src.client.tools.camera import CAMERA_TOOL_DECLARATION, execute_camera_tool
from src.client.tools.memories import MEMORIES_TOOL_DECLARATION, execute_memories_tool
from src.client.tools.messages import MESSAGES_TOOL_DECLARATION, execute_messages_tool
from src.client.tools.move import MOVE_TOOL_DECLARATION, execute_move_tool
from src.client.tools.react import REACT_TOOL_DECLARATION, execute_react_tool
from src.client.tools.tasks import TASKS_TOOL_DECLARATION, execute_tasks_tool
from src.client.tools.current_time import (
    CURRENT_TIME_TOOL_DECLARATION,
    execute_current_time_tool,
)
from src.client.tools.people import ADD_FACE_TOOL_DECLARATION, execute_add_face_tool
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
    "CAMERA_TOOL_DECLARATION",
    "ADD_FACE_TOOL_DECLARATION",
    "MEMORIES_TOOL_DECLARATION",
    "MESSAGES_TOOL_DECLARATION",
    "MOVE_TOOL_DECLARATION",
    "CURRENT_TIME_TOOL_DECLARATION",
    "MODEL_TOOL_POLICY",
    "REACT_TOOL_DECLARATION",
    "TASKS_TOOL_DECLARATION",
    "TOOL_SCHEMAS",
    "ToolContext",
    "build_gemini_tool",
    "build_gemini_tools",
    "build_llama_tools",
    "dispatch_tool_call",
    "execute_camera_tool",
    "execute_add_face_tool",
    "execute_memories_tool",
    "execute_messages_tool",
    "execute_move_tool",
    "execute_current_time_tool",
    "execute_react_tool",
    "execute_tasks_tool",
    "tool_names_for",
    "tool_schemas_for",
]
