"""
Execution logic for the 'messages' tool call.
Searches Cubey's stored conversation history via semantic embeddings and
returns the closest matching past messages.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 5
_MAX_LIMIT = 10


def execute_messages_tool(
    query: str,
    limit: Optional[int] = None,
    embedding_service=None,
) -> dict[str, Any]:
    """
    Executes the 'messages' tool call received from Gemini Live API.

    Searches stored message embeddings and returns a plain dict suitable for
    send_tool_response. Never raises: errors are returned as a status dict so
    the model can react gracefully.
    """
    clean_query = str(query or "").strip()
    try:
        clean_limit = int(limit) if limit is not None else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        clean_limit = _DEFAULT_LIMIT
    clean_limit = max(1, min(clean_limit, _MAX_LIMIT))

    if not clean_query:
        return {
            "status": "no_query",
            "matches": [],
            "message": "No search query was provided.",
        }

    logger.info("🔍 AI Tool Call Executed: messages(query='%s', limit=%d)", clean_query, clean_limit)

    try:
        from src.services.message_service import search_messages

        matches = search_messages(
            clean_query, limit=clean_limit, embedding_service=embedding_service
        )
    except Exception as e:
        logger.exception("messages tool search failed: %s", e)
        return {
            "status": "search_error",
            "matches": [],
            "message": f"Memory search failed: {e}",
        }

    if not matches:
        return {
            "status": "no_results",
            "query": clean_query,
            "matches": [],
            "message": "No stored conversations matched that query.",
        }

    return {
        "status": "search_complete",
        "query": clean_query,
        "matches": matches,
    }
