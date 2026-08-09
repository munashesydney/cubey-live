"""
Durable memory service — the "actual memory" layer.

Composes the embedding service with the memories tables to give the AI a way
to store and recall facts about the user that persist across sessions:

  - add:    embed + store a new memory (skips near-duplicates)
  - update: edit an existing memory in place (re-embeds on content change)
  - search: semantic recall over active memories, with a keyword fallback

Every function returns a plain dict suitable for a tool response.
"""

import logging
from typing import Optional

import numpy as np

from src.db import (
    MemoryStatus,
    create_memory as _create_memory_row,
    get_memory as _get_memory_row,
    keyword_search_memories,
    list_memories,
    list_memory_embeddings,
    save_memory_embedding,
    update_memory as _update_memory_row,
)
from src.db.repositories.embeddings import decode_embedding
from src.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

# Cosine scores above this mean "same memory" -> skip the duplicate.
# Measured: exact duplicates ~1.0, close paraphrases ~0.90.
_DUPLICATE_THRESHOLD = 0.88
# Minimum cosine for a semantic search hit.
_MIN_SCORE = 0.15


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def add_memory(
    content: str,
    category: Optional[str] = None,
    importance: Optional[int] = None,
    source_message_id: Optional[int] = None,
    source_conversation_id: Optional[int] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> dict:
    """
    Store a new durable memory. Returns a dict with `status` one of:
    'created', 'exists' (near-duplicate skipped), or 'missing_content'.
    """
    clean = str(content or "").strip()
    if not clean:
        return {
            "status": "missing_content",
            "memory_id": None,
            "message": "No memory content was provided.",
        }

    service = embedding_service or EmbeddingService()

    # Embedding is best-effort: if it fails, the memory is still stored and
    # remains reachable via keyword search.
    vector = None
    try:
        vector = service.embed(clean)
    except Exception as e:
        logger.warning("Embedding unavailable for new memory: %s", e)

    if vector is not None:
        duplicate = _find_duplicate(vector, service.model_name)
        if duplicate is not None:
            logger.info("Skipped near-duplicate memory #%s: %r", duplicate.id, duplicate.content[:50])
            return {
                "status": "exists",
                "memory_id": duplicate.id,
                "content": duplicate.content,
                "message": "A nearly identical memory is already stored.",
            }

    memory = _create_memory_row(
        content=clean,
        category=category,
        importance=importance if importance is not None else 5,
        source_message_id=source_message_id,
        source_conversation_id=source_conversation_id,
    )
    if vector is not None:
        save_memory_embedding(memory.id, service.model_name, vector)

    logger.info("Stored new memory #%s: %r", memory.id, clean[:60])
    return {
        "status": "created",
        "memory_id": memory.id,
        "content": memory.content,
        "message": "Memory stored.",
    }


def _find_duplicate(
    vector: np.ndarray,
    model_name: str,
    threshold: float = _DUPLICATE_THRESHOLD,
):
    """Return the active Memory most similar to `vector` if it clears the
    duplicate threshold, else None."""
    rows = list_memory_embeddings(model_name=model_name)
    if not rows:
        return None
    active_ids = {m.id for m in list_memories(status=MemoryStatus.ACTIVE)}
    rows = [r for r in rows if r.memory_id in active_ids]
    if not rows:
        return None

    matrix = np.stack([decode_embedding(r.embedding) for r in rows])
    norms = np.linalg.norm(matrix, axis=1)
    query_norm = np.linalg.norm(vector)
    if query_norm == 0.0 or (norms == 0.0).all():
        return None

    scores = (matrix @ vector) / (norms * query_norm)
    best_idx = int(np.argmax(scores))
    if scores[best_idx] >= threshold:
        return _get_memory_row(rows[best_idx].memory_id)
    return None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def update_memory(
    memory_id: int,
    content: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[int] = None,
    embedding_service: Optional[EmbeddingService] = None,
) -> dict:
    """
    Edit an existing memory in place (None fields are left unchanged).
    Returns `status` one of 'updated', 'not_found'.
    """
    existing = _get_memory_row(memory_id)
    if existing is None:
        return {
            "status": "not_found",
            "memory_id": memory_id,
            "message": f"No memory with id {memory_id}.",
        }

    new_content = str(content or "").strip() if content is not None else None
    if new_content == "":
        new_content = None  # empty string means "unchanged" like None

    # Re-embed only when the content actually changes.
    if new_content is not None and new_content != existing.content:
        service = embedding_service or EmbeddingService()
        try:
            vector = service.embed(new_content)
        except Exception as e:
            logger.warning("Embedding unavailable while updating memory #%s: %s", memory_id, e)
            vector = None
        if vector is not None:
            save_memory_embedding(memory_id, service.model_name, vector)

    updated = _update_memory_row(
        memory_id,
        content=new_content,
        category=category,
        importance=importance,
    )
    if updated is None:  # defensive: row vanished between get and update
        return {
            "status": "not_found",
            "memory_id": memory_id,
            "message": f"No memory with id {memory_id}.",
        }
    return {
        "status": "updated",
        "memory_id": memory_id,
        "content": updated.content,
        "category": updated.category,
        "importance": updated.importance,
        "message": "Memory updated.",
    }


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def search_memories(
    query: str,
    limit: int = 5,
    embedding_service: Optional[EmbeddingService] = None,
) -> list[dict]:
    """
    Semantic recall over active memories. Falls back to a LIKE keyword scan
    when no embeddings exist or nothing scores high enough.

    Returns dicts: {memory_id, content, category, importance, created_at,
    score} — `score` is a cosine in (0,1] for semantic hits and None for
    keyword fallback hits.
    """
    clean = str(query or "").strip()
    if not clean:
        return []

    service = embedding_service or EmbeddingService()
    rows = list_memory_embeddings(model_name=service.model_name)

    if rows:
        active_by_id = {m.id: m for m in list_memories(status=MemoryStatus.ACTIVE)}
        pairs = [(r, active_by_id[r.memory_id]) for r in rows if r.memory_id in active_by_id]
        if pairs:
            try:
                query_vector = service.embed_query(clean)
            except Exception as e:
                logger.warning("Embedding unavailable for memory search: %s", e)
                query_vector = None
            if query_vector is not None:
                matrix = np.stack([decode_embedding(r.embedding) for r, _ in pairs])
                norms = np.linalg.norm(matrix, axis=1)
                query_norm = np.linalg.norm(query_vector)
                if query_norm > 0.0 and (norms > 0.0).any():
                    scores = (matrix @ query_vector) / (norms * query_norm)
                    order = np.argsort(-scores)
                    results: list[dict] = []
                    for idx in order:
                        if len(results) >= limit:
                            break
                        score = float(scores[idx])
                        if score < _MIN_SCORE:
                            break
                        memory = pairs[idx][1]
                        results.append(_memory_dict(memory, score))
                    if results:
                        return results

    # Keyword fallback (also covers "no embeddings stored yet").
    logger.info("Memory search falling back to keyword match for %r", clean)
    matches = keyword_search_memories(clean, status=MemoryStatus.ACTIVE, limit=limit)
    return [_memory_dict(m, None) for m in matches]


def _memory_dict(memory, score: Optional[float]) -> dict:
    return {
        "memory_id": memory.id,
        "content": memory.content,
        "category": memory.category,
        "importance": memory.importance,
        "score": round(score, 4) if score is not None else None,
        "created_at": memory.created_at.isoformat(timespec="seconds") if memory.created_at else "",
    }
