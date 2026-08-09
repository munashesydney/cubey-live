"""
Semantic memory over past conversation messages.

Combines the embedding service (fastembed) with the message_embeddings table
to find messages that are semantically similar to a query. Pure numpy cosine
similarity — more than fast enough for a personal robot's message history.

The search is intentionally unscoped (any conversation, any time) so the AI
can recall anything Cubey has ever been told.
"""

import logging
from datetime import datetime
from typing import Optional

import numpy as np

from src.db import (
    decode_embedding,
    get_message,
    list_embeddings,
)
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

# Score floor — cosine below this is unlikely to be a meaningful match.
_MIN_SCORE = 0.15


def search_messages(
    query: str,
    limit: int = 5,
    model_name: Optional[str] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> list[dict]:
    """
    Search stored messages by semantic similarity to `query`.

    Returns up to `limit` results as plain dicts, best match first:
      {message_id, conversation_id, role, content, score, created_at}
    """
    if not query.strip():
        return []

    service = embedding_service or EmbeddingService()
    model = model_name or service.model_name

    query_vector = service.embed_query(query.strip())
    rows = list_embeddings(model_name=model)
    if not rows:
        logger.info("No stored embeddings for model '%s' yet", model)
        return []

    matrix = np.stack([decode_embedding(row.embedding) for row in rows])
    norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(query_vector)
    if query_norm == 0.0 or (norms == 0.0).all():
        return []

    scores = (matrix @ query_vector) / (norms * query_norm)

    # Descending score order.
    order = np.argsort(-scores)
    results: list[dict] = []
    for idx in order:
        if len(results) >= limit:
            break
        score = float(scores[idx])
        if score < _MIN_SCORE:
            break
        row = rows[idx]
        message = get_message(row.message_id)
        if message is None:
            continue
        results.append(
            {
                "message_id": message.id,
                "conversation_id": message.conversation_id,
                "role": message.role.value,
                "content": message.content,
                "score": round(score, 4),
                "created_at": _iso(message.created_at),
            }
        )
    return results


def _iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return value.isoformat(timespec="seconds")
