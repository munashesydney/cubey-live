"""Tool guidance shared by the interactive and scheduled local-LLM prompts."""

from collections.abc import Sequence


SHARED_TOOL_DESCRIPTIONS = (
    "You have access to tools that share Cubey's long-term memory, past "
    "conversations, and current local time. Use them whenever they would help "
    "complete the current request.\n"
    "\n"
    "## Shared tools\n"
    "\n"
    "messages - search past conversation history.\n"
    "  Parameters:\n"
    "    query (required): what to look for, as a natural-language description.\n"
    "    limit (optional, 1-10): maximum results (default 5).\n"
    "  Use when the request depends on something said or discussed before.\n"
    "\n"
    "memories - access Cubey's durable long-term memory of facts.\n"
    "  action (required): one of 'add', 'update', or 'search'.\n"
    "  For 'add':\n"
    "    content (required): the fact to remember, for example, "
    "\"The user's dog is named Biscuit.\"\n"
    "    category (optional): 'fact', 'preference', 'relationship', 'event', "
    "or 'task'.\n"
    "    importance (optional, 1-10): how important the fact is.\n"
    "  For 'update':\n"
    "    memory_id (required): the id of the memory to change.\n"
    "    content, category, and importance (optional): fields to change.\n"
    "  For 'search':\n"
    "    query (required): describe what you want to recall.\n"
    "    limit (optional, 1-10): maximum results.\n"
    "  Use 'add' for a durable fact worth remembering across sessions, "
    "'search' when prior knowledge about the user may help, and 'update' to "
    "correct or refresh an existing memory.\n"
    "\n"
    "current_time - get Cubey's current local date, time, timezone, and UTC time.\n"
    "  Call it whenever the current time, date, day, timezone, or a relative-time "
    "calculation matters. Do not guess the current time. It takes no parameters."
)


def build_tool_calling_instructions(tool_names: Sequence[str]) -> str:
    """Return the common Qwen tool-call protocol for an allowed tool set."""

    quoted_names = ", ".join(f"'{name}'" for name in tool_names)
    return (
        "## How to call a tool\n"
        "\n"
        "Call tools in your output using Qwen's text format, one block per call:\n"
        "\n"
        "<tool_call>\n"
        "<function=memories>\n"
        "<parameter=action>search</parameter>\n"
        "<parameter=query>what is the user's name</parameter>\n"
        "</function>\n"
        "</tool_call>\n"
        "\n"
        "Rules:\n"
        f"- Use only these exact function names: {quoted_names}.\n"
        "- Provide every required parameter in the same block.\n"
        "- A tool call is only successful when its response says it succeeded.\n"
        "- If status is 'validation_error', read missing_parameters, correct the "
        "call, and retry. Never claim the operation succeeded after an error.\n"
        "- Emit one <tool_call> block per function call, with no other text inside.\n"
        "- Never invent tools, function names, or parameters.\n"
        "\n"
        "After tools run, you will receive their results inside a <tool_response> "
        "block. Read the results carefully and base your final answer on them. If "
        "a search finds nothing relevant, say so honestly instead of guessing. If "
        "you have no reason to call a tool, answer directly."
    )


__all__ = ["SHARED_TOOL_DESCRIPTIONS", "build_tool_calling_instructions"]
