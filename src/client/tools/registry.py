"""
Tool registry — the single source of truth for Cubey's AI tools.

Owns:
  - TOOL_SCHEMAS:      the neutral, OpenAI-style definitions (name, description,
                       JSON-schema parameters) for every tool.
  - MODEL_TOOL_POLICY: which tools each model may call:
                         live_model -> react, messages, memories
                         local_model -> messages, memories (no face reactions)
  - builders:          render the neutral schemas into Gemini Live
                       declarations or llama.cpp tool lists.
  - dispatch:          route a tool call by name to its executor.

Nothing else in the codebase should duplicate a tool's schema or policy.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Neutral tool schemas (OpenAI-style JSON schema)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "react": {
        "name": "react",
        "description": (
            "Triggers a visual facial emotion reaction on the robot's screen display. "
            "Call this tool whenever you experience physical interactions (e.g. being "
            "kicked, moved), or whenever your emotional tone shifts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reaction_type": {
                    "type": "string",
                    "description": (
                        "The visual reaction type to express on the display: "
                        "'hurt' (when kicked or damaged), 'alert' (when obstacle detected or moved), "
                        "'happy' (warm/friendly), 'surprised' (shocked/unexpected), "
                        "'skeptical' (confused/questioning), 'low_battery' (power low), or 'normal'."
                    ),
                }
            },
            "required": ["reaction_type"],
        },
    },
    "messages": {
        "name": "messages",
        "description": (
            "Search Cubey's memory of past conversation messages by semantic meaning. "
            "Call this tool when you need to recall something the user said before, "
            "anything discussed in an earlier conversation, or facts about the user's "
            "life. Returns the most similar past messages with their content, role, "
            "and when they happened."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A natural-language description of what to look for, e.g. "
                        "'what did the user say about their dog' or 'when did we talk "
                        "about the kitchen project'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matching messages to return (1-10).",
                },
            },
            "required": ["query"],
        },
    },
    "memories": {
        "name": "memories",
        "description": (
            "Manage Cubey's long-term memory of durable facts about the user. "
            "Use 'add' to store a new fact the user shares that is worth "
            "remembering across sessions (preferences, names, life details). "
            "Use 'update' to correct or refine a stored memory by id. "
            "Use 'search' to recall stored memories relevant to the current "
            "conversation. Memories persist forever and are distinct from message "
            "history."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "search"],
                    "description": "What to do with memory.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The memory text itself, phrased as a fact, e.g. "
                        "'The user's dog is named Biscuit'. Required for 'add'."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional category: 'fact', 'preference', 'relationship', "
                        "'event', or 'task'."
                    ),
                },
                "importance": {
                    "type": "integer",
                    "description": "How important the memory is, 1 (trivial) to 10 (critical).",
                },
                "memory_id": {
                    "type": "integer",
                    "description": "Id of the memory to update. Required for 'update'.",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Natural-language description of what to recall. Required "
                        "for 'search'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return (1-10).",
                },
            },
            "required": ["action"],
        },
    },
    "tasks": {
        "name": "tasks",
        "description": (
            "Schedule tasks that spawn an AI to do something later. "
            "A task runs one AI ('local' Qwen or 'gemini') with a prompt when "
            "its schedule is due. Use 'add' to schedule something, 'list' to see "
            "scheduled tasks, 'update' to change one, 'delete' to cancel one. "
            "Examples: remind the user at a time, run a daily summary, or do a "
            "quick check every few minutes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "update", "delete"],
                    "description": "What to do with tasks.",
                },
                "title": {
                    "type": "string",
                    "description": "Short name for the task, e.g. 'Evening reminder'.",
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The instruction the AI runs when the task is due, e.g. "
                        "'Remind the user to take their medicine'."
                    ),
                },
                "model": {
                    "type": "string",
                    "enum": ["local", "gemini"],
                    "description": "Which AI runs the task: 'local' (Qwen) or 'gemini'.",
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["one_shot", "interval", "cron"],
                    "description": (
                        "'one_shot' runs once at run_at; 'interval' runs every "
                        "interval_seconds; 'cron' runs per cron_expr."
                    ),
                },
                "run_at": {
                    "type": "string",
                    "description": (
                        "ISO-8601 timestamp for 'one_shot' schedules, interpreted "
                        "in the machine's local timezone, e.g. '2026-08-09T19:00:00'. "
                        "For 'in 5 seconds', use the current time plus 5 seconds."
                    ),
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": "Seconds between runs for 'interval' schedules (e.g. 300 = every 5 minutes).",
                },
                "cron_expr": {
                    "type": "string",
                    "description": (
                        "5-field cron expression for 'cron' schedules, in local time, "
                        "e.g. '0 19 * * *' for every day at 7pm or '*/5 * * * *' "
                        "for every 5 minutes."
                    ),
                },
                "task_id": {
                    "type": "integer",
                    "description": "Id of the task to update or delete.",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "paused", "done"],
                    "description": "Optional new status for 'update' (e.g. 'paused').",
                },
            },
            "required": ["action"],
        },
    },
}

# ---------------------------------------------------------------------------
# Model tool policy — which tools each model may call.
# ---------------------------------------------------------------------------

MODEL_TOOL_POLICY: dict[str, list[str]] = {
    # Gemini Live: full agent — physical reactions + memory + history + tasks.
    "live_model": ["react", "messages", "memories", "tasks"],
    # Local Qwen: the user never interacts with it directly, so it has no
    # physical reactions — but it shares the memory bank, history, and can
    # schedule tasks.
    "local_model": ["messages", "memories", "tasks"],
}


def tool_names_for(model_key: str) -> list[str]:
    """The tool names a model is allowed to call."""
    return list(MODEL_TOOL_POLICY.get(model_key, []))


def tool_schemas_for(model_key: str) -> list[dict[str, Any]]:
    """The neutral schemas a model is allowed to call."""
    return [TOOL_SCHEMAS[name] for name in tool_names_for(model_key)]


# ---------------------------------------------------------------------------
# Gemini Live adapter
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "object": types.Type.OBJECT,
    "array": types.Type.ARRAY,
}


def _gemini_schema(schema: dict[str, Any]) -> types.Schema:
    """Convert a neutral JSON schema into a google.genai types.Schema."""
    type_name = schema.get("type", "object")
    if schema.get("enum"):
        return types.Schema(
            type=_TYPE_MAP.get(type_name, types.Type.STRING),
            enum=schema["enum"],
            description=schema.get("description"),
        )
    properties = schema.get("properties")
    return types.Schema(
        type=_TYPE_MAP.get(type_name, types.Type.OBJECT),
        description=schema.get("description"),
        properties=(
            {key: _gemini_schema(value) for key, value in properties.items()}
            if properties
            else None
        ),
        required=schema.get("required"),
    )


def build_gemini_tool(name: str) -> types.Tool:
    """Render one neutral tool schema as a Gemini Live Tool declaration."""
    schema = TOOL_SCHEMAS[name]
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=schema["name"],
                description=schema["description"],
                parameters=_gemini_schema(schema["parameters"]),
            )
        ]
    )


def build_gemini_tools(model_key: str) -> list[types.Tool]:
    """Render the allowed tool schemas as Gemini Live Tool declarations."""
    return [build_gemini_tool(name) for name in tool_names_for(model_key)]


# ---------------------------------------------------------------------------
# llama.cpp adapter
# ---------------------------------------------------------------------------

def build_llama_tools(model_key: str) -> list[dict[str, Any]]:
    """Render the allowed tool schemas in OpenAI-style tool list form
    (what llama.cpp's create_chat_completion expects)."""
    return [
        {"type": "function", "function": json.loads(json.dumps(schema))}
        for schema in tool_schemas_for(model_key)
    ]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Everything an executor needs, regardless of which model called it."""

    embedding_service: Optional[Any] = None
    on_react: Optional[Callable[[str], None]] = None


def dispatch_tool_call(name: str, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    """Route a tool call to its executor. Never raises — returns a result dict."""
    # Lazy imports keep the registry free of heavy deps at import time.
    try:
        if name == "react":
            from src.client.tools.react import execute_react_tool

            return execute_react_tool(
                reaction_type=args.get("reaction_type", "hurt"),
                on_trigger_reaction=context.on_react,
            )

        if name == "messages":
            from src.client.tools.messages import execute_messages_tool

            return execute_messages_tool(
                query=args.get("query", ""),
                limit=args.get("limit"),
                embedding_service=context.embedding_service,
            )

        if name == "memories":
            from src.client.tools.memories import execute_memories_tool

            return execute_memories_tool(
                action=args.get("action"),
                content=args.get("content"),
                category=args.get("category"),
                importance=args.get("importance"),
                memory_id=args.get("memory_id"),
                query=args.get("query"),
                limit=args.get("limit"),
                embedding_service=context.embedding_service,
            )

        if name == "tasks":
            from src.client.tools.tasks import execute_tasks_tool

            return execute_tasks_tool(
                action=args.get("action"),
                title=args.get("title"),
                prompt=args.get("prompt"),
                model=args.get("model"),
                schedule_type=args.get("schedule_type"),
                run_at=args.get("run_at"),
                interval_seconds=args.get("interval_seconds"),
                cron_expr=args.get("cron_expr"),
                task_id=args.get("task_id"),
                status=args.get("status"),
            )
    except Exception as e:
        logger.exception("Tool dispatch failed for '%s': %s", name, e)
        return {"status": "tool_error", "message": f"Tool execution failed: {e}"}

    return {"status": "unknown_tool", "message": f"Unknown tool '{name}'."}
