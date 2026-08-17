"""Persistence format for local-Qwen tool calls and their responses."""

import html
import json
from typing import Any, Optional


TOOL_TRACE_PREFIX = "cubey.local_tool_trace.v1:"


def serialize_tool_trace(name: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """Serialize one completed tool interaction into an EVENT message."""

    return TOOL_TRACE_PREFIX + json.dumps(
        {"name": name, "args": args, "result": result},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )


def deserialize_tool_trace(content: str) -> Optional[dict[str, Any]]:
    """Decode a trace EVENT, returning None for unrelated or malformed events."""

    if not content.startswith(TOOL_TRACE_PREFIX):
        return None
    try:
        trace = json.loads(content[len(TOOL_TRACE_PREFIX):])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(trace, dict) or not isinstance(trace.get("name"), str):
        return None
    if not isinstance(trace.get("args"), dict) or not isinstance(trace.get("result"), dict):
        return None
    return trace


def tool_trace_messages(content: str) -> list[dict[str, str]]:
    """Rebuild the text tool-call exchange Qwen saw during the original turn."""

    trace = deserialize_tool_trace(content)
    if trace is None:
        return []

    parameter_lines = []
    for key, value in trace["args"].items():
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parameter_lines.append(
            f"<parameter={html.escape(str(key))}>{html.escape(str(rendered))}</parameter>"
        )
    parameters = "\n".join(parameter_lines)
    call = (
        "<tool_call>\n"
        f"<function={html.escape(trace['name'])}>\n"
        f"{parameters}\n"
        "</function>\n"
        "</tool_call>"
    )
    response = (
        "<tool_response>"
        + json.dumps(trace["result"], ensure_ascii=False, default=str)
        + "</tool_response>"
    )
    return [
        {"role": "assistant", "content": call},
        {"role": "user", "content": response},
    ]


__all__ = [
    "TOOL_TRACE_PREFIX",
    "deserialize_tool_trace",
    "serialize_tool_trace",
    "tool_trace_messages",
]
