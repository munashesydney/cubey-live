"""
Execution logic for the 'memories' tool call.
Adds, updates, or searches Cubey's durable long-term memory.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10


def execute_memories_tool(
    action: Optional[str] = None,
    content: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[int] = None,
    memory_id: Optional[int] = None,
    query: Optional[str] = None,
    limit: Optional[int] = None,
    embedding_service=None,
) -> dict[str, Any]:
    """
    Executes the 'memories' tool call received from Gemini Live API.

    Dispatches on `action` (add/update/search) to the memory service and
    returns a plain dict suitable for send_tool_response. Never raises:
    errors are returned as status dicts so the model can react gracefully.
    """
    clean_action = str(action or "").lower().strip()

    # --- argument hygiene ---------------------------------------------------
    try:
        clean_limit = int(limit) if limit is not None else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        clean_limit = _DEFAULT_LIMIT
    clean_limit = max(1, min(clean_limit, _MAX_LIMIT))

    try:
        clean_importance = int(importance) if importance is not None else None
    except (TypeError, ValueError):
        clean_importance = None

    try:
        clean_memory_id = int(memory_id) if memory_id is not None else None
    except (TypeError, ValueError):
        clean_memory_id = None

    logger.info(
        "🧠 AI Tool Call Executed: memories(action='%s', memory_id=%s, query='%s')",
        clean_action, clean_memory_id, (query or "")[:60],
    )

    try:
        from src.services.memory_service import add_memory, search_memories, update_memory

        if clean_action == "add":
            result = add_memory(
                content=content or "",
                category=category,
                importance=clean_importance,
                embedding_service=embedding_service,
            )
            return result

        if clean_action == "update":
            if clean_memory_id is None:
                return {
                    "status": "missing_memory_id",
                    "memory_id": None,
                    "message": "Updating a memory requires its memory_id.",
                }
            result = update_memory(
                memory_id=clean_memory_id,
                content=content,
                category=category,
                importance=clean_importance,
                embedding_service=embedding_service,
            )
            return result

        if clean_action == "search":
            clean_query = str(query or "").strip()
            if not clean_query:
                return {
                    "status": "missing_query",
                    "matches": [],
                    "message": "Searching memories requires a query.",
                }
            matches = search_memories(
                clean_query, limit=clean_limit, embedding_service=embedding_service
            )
            if not matches:
                return {
                    "status": "no_results",
                    "query": clean_query,
                    "matches": [],
                    "message": "No stored memories matched that query.",
                }
            return {
                "status": "search_complete",
                "query": clean_query,
                "matches": matches,
            }

        return {
            "status": "invalid_action",
            "message": (
                f"Unknown action '{action}'. Valid actions: add, update, search."
            ),
        }

    except Exception as e:
        logger.exception("memories tool failed: %s", e)
        return {
            "status": "memory_error",
            "message": f"Memory operation failed: {e}",
        }
