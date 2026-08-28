"""
Tool registry — the single source of truth for Cubey's AI tools.

Owns:
  - TOOL_SCHEMAS:      the neutral, OpenAI-style definitions (name, description,
                       JSON-schema parameters) for every tool.
  - MODEL_TOOL_POLICY: which tools each model may call:
                         live_model -> react, move, messages, memories, current_time, tasks
                         local_model -> messages, memories, current_time, tasks
                         local_task_runner -> messages, memories, current_time
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
                        "'skeptical' (confused/questioning), 'low_battery' (power low), "
                        "'charging' (connected to power/charging), 'sleeping' (resting/peaceful), or 'normal'."
                    ),
                }
            },
            "required": ["reaction_type"],
        },
    },
    "move": {
        "name": "move",
        "description": (
            "Controls Cubey's physical 4-wheel mecanum drive base. "
            "Call this tool to drive forward, backward, turn/rotate in place, "
            "drive diagonally, or stop. (Note: sideways strafing is disabled). "
            "Use duration_seconds to control how long Cubey moves before stopping automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "forward",
                        "backward",
                        "rotate_left",
                        "rotate_right",
                        "forward_left",
                        "forward_right",
                        "backward_left",
                        "backward_right",
                        "stop",
                    ],
                    "description": (
                        "The direction or motion to perform: "
                        "'forward' (drive straight ahead), "
                        "'backward' (drive in reverse), "
                        "'rotate_left' (spin left in place), "
                        "'rotate_right' (spin right in place), "
                        "'forward_left' (diagonal forward-left), "
                        "'forward_right' (diagonal forward-right), "
                        "'backward_left' (diagonal backward-left), "
                        "'backward_right' (diagonal backward-right), "
                        "or 'stop' (halt all motors immediately)."
                    ),
                },
                "duration_seconds": {
                    "type": "number",
                    "description": (
                        "How long to move in seconds before stopping automatically "
                        "(e.g. 0.5 for a quick tap/step, 1.0, 2.0). Defaults to 1.0 second. "
                        "Ignored if action is 'stop'."
                    ),
                },
                "speed": {
                    "type": "integer",
                    "description": (
                        "Optional motor speed between 70 (slow) and 255 (maximum speed). "
                        "Default is 180."
                    ),
                },
            },
            "required": ["action"],
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
    "current_time": {
        "name": "current_time",
        "description": (
            "Get the current date and time from Cubey's machine. Call this whenever "
            "the current time, date, day, timezone, or a relative time expression "
            "such as 'in two hours' matters. Do not guess the time."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "tasks": {
        "name": "tasks",
        "description": (
            "Schedule work for an AI pipeline to perform later. "
            "A task runs one AI ('local' Qwen or 'gemini') with a prompt when "
            "its schedule is due. Use 'add' to schedule something, 'list' to see "
            "scheduled tasks, 'update' to change one, 'delete' to cancel one. "
            "Examples: remind the user at a time, run a daily summary, or do a "
            "quick check every few minutes. For action='add', always provide "
            "title, prompt, model, schedule_type, and the schedule-specific field. "
            "Write prompt as an instruction to your future self that preserves "
            "the user's perspective; for example, 'Remind the user to call their "
            "dad', not 'Call your dad'."
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
                    "description": (
                        "Required for 'add'. Short name for the task, e.g. "
                        "'Evening reminder'."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Required for 'add'. An instruction written to the AI that "
                        "will run it later. Preserve who the user is: write "
                        "'Remind the user to call their dad', not 'Call your dad'."
                    ),
                },
                "model": {
                    "type": "string",
                    "enum": ["local", "gemini"],
                    "description": (
                        "Required for 'add'. Which AI runs the task: 'local' "
                        "(Qwen) or 'gemini'."
                    ),
                },
                "schedule_type": {
                    "type": "string",
                    "enum": ["one_shot", "interval", "cron"],
                    "description": (
                        "Required for 'add'. 'one_shot' runs once at run_at; "
                        "'interval' runs every "
                        "interval_seconds; 'cron' runs per cron_expr."
                    ),
                },
                "run_at": {
                    "type": "string",
                    "description": (
                        "Required when schedule_type is 'one_shot'. ISO-8601 "
                        "timestamp interpreted "
                        "in the machine's local timezone, e.g. '2026-08-09T19:00:00'. "
                        "For 'in 5 seconds', use the current time plus 5 seconds."
                    ),
                },
                "interval_seconds": {
                    "type": "integer",
                    "description": (
                        "Required when schedule_type is 'interval'. Seconds between "
                        "runs (e.g. 300 = every 5 minutes)."
                    ),
                },
                "cron_expr": {
                    "type": "string",
                    "description": (
                        "Required when schedule_type is 'cron'. A 5-field cron "
                        "expression in local time, "
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
    # Gemini Live: full robot agent with physical movement, reactions, memory, history, tasks.
    "live_model": ["react", "move", "messages", "memories", "current_time", "tasks"],
    # Interactive local Qwen (no physical movement tool per design).
    "local_model": ["messages", "memories", "current_time", "tasks"],
    # Scheduled runs execute an existing task.
    "local_task_runner": ["messages", "memories", "current_time"],
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
    "number": types.Type.NUMBER,
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
    wheels_service: Optional[Any] = None


def validate_tool_call(name: str, args: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Return actionable feedback for missing action-specific parameters."""

    schema = TOOL_SCHEMAS.get(name)
    if schema is None:
        return {
            "status": "unknown_tool",
            "tool": name,
            "message": f"Unknown tool '{name}'.",
        }

    required = list(schema["parameters"].get("required", []))
    action = str(args.get("action") or "").strip().lower()

    if name == "memories":
        required.extend(
            {
                "add": ["content"],
                "update": ["memory_id"],
                "search": ["query"],
            }.get(action, [])
        )
    elif name == "tasks":
        if action == "add":
            required.extend(["title", "prompt", "model", "schedule_type"])
            schedule_type = str(args.get("schedule_type") or "").strip().lower()
            schedule_field = {
                "one_shot": "run_at",
                "interval": "interval_seconds",
                "cron": "cron_expr",
            }.get(schedule_type)
            if schedule_field:
                required.append(schedule_field)
        elif action in {"update", "delete"}:
            required.append("task_id")

    missing = list(
        dict.fromkeys(
            field
            for field in required
            if field not in args
            or args[field] is None
            or (isinstance(args[field], str) and not args[field].strip())
        )
    )
    if missing:
        operation = f"{name}.{action}" if action else name
        return {
            "status": "validation_error",
            "tool": name,
            "action": action or None,
            "missing_parameters": missing,
            "required_parameters": list(dict.fromkeys(required)),
            "message": (
                f"{operation} was not executed because required parameter(s) are "
                f"missing: {', '.join(missing)}. Call the tool again with every "
                "required parameter. Do not tell the user it succeeded yet."
            ),
        }

    if name == "tasks" and action == "add":
        prompt = str(args.get("prompt") or "").strip()
        lowered_prompt = prompt.lower()
        personal_action = lowered_prompt.startswith(
            ("call ", "text ", "email ", "visit ", "take ", "meet ", "contact ")
        )
        wrong_perspective = "remind me" in lowered_prompt or (
            personal_action
            and any(token in f" {lowered_prompt} " for token in (" my ", " your ", " me "))
            and " user" not in lowered_prompt
        )
        if wrong_perspective:
            return {
                "status": "validation_error",
                "tool": name,
                "action": action,
                "invalid_parameters": ["prompt"],
                "message": (
                    "tasks.add was not executed because prompt uses the wrong "
                    "perspective. Write it as an instruction to your future self "
                    "and refer to the person as 'the user'. Example: 'Remind the "
                    "user to call their dad', not 'Call your dad'. Call tasks.add "
                    "again with a corrected prompt."
                ),
            }

    return None


def dispatch_tool_call(name: str, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    """Route a tool call to its executor. Never raises — returns a result dict."""
    validation_error = validate_tool_call(name, args)
    if validation_error is not None:
        logger.warning(
            "Rejected invalid tool call %s: %s",
            name,
            validation_error["message"],
        )
        return validation_error

    # Lazy imports keep the registry free of heavy deps at import time.
    try:
        if name == "react":
            from src.client.tools.react import execute_react_tool

            return execute_react_tool(
                reaction_type=args.get("reaction_type", "hurt"),
                on_trigger_reaction=context.on_react,
            )

        if name == "move":
            from src.client.tools.move import execute_move_tool

            return execute_move_tool(
                action=args.get("action", "forward"),
                duration_seconds=float(args.get("duration_seconds", 1.0)),
                speed=int(args["speed"]) if args.get("speed") is not None else None,
                wheels_service=context.wheels_service,
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

        if name == "current_time":
            from src.client.tools.current_time import execute_current_time_tool

            return execute_current_time_tool()

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
