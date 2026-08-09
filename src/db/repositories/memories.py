"""
CRUD operations for the memories and memory_embeddings tables.

Same session contract as the other repositories: each call opens and commits
its own short-lived session unless an explicit `session` is passed in.
"""

import logging
from typing import Optional

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.db.models import Memory, MemoryEmbedding, MemoryStatus
from src.db.repositories.embeddings import encode_embedding
from src.db.session import get_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# memories
# ---------------------------------------------------------------------------

def create_memory(
    content: str,
    category: Optional[str] = None,
    importance: int = 5,
    source_message_id: Optional[int] = None,
    source_conversation_id: Optional[int] = None,
    session: Optional[Session] = None,
) -> Memory:
    """Create a new ACTIVE memory row."""

    def _create(s: Session) -> Memory:
        memory = Memory(
            content=content,
            category=(category or None) and category.strip()[:32] or None,
            importance=max(1, min(10, int(importance))),
            source_message_id=source_message_id,
            source_conversation_id=source_conversation_id,
        )
        s.add(memory)
        s.flush()
        return memory

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def get_memory(
    memory_id: int,
    session: Optional[Session] = None,
) -> Optional[Memory]:
    """Fetch a single memory by primary key."""

    def _get(s: Session) -> Optional[Memory]:
        return s.get(Memory, memory_id)

    if session is not None:
        return _get(session)
    with get_session() as s:
        return _get(s)


def update_memory(
    memory_id: int,
    *,
    content: Optional[str] = None,
    category: Optional[str] = None,
    importance: Optional[int] = None,
    status: Optional[MemoryStatus] = None,
    session: Optional[Session] = None,
) -> Optional[Memory]:
    """Update a memory. None arguments are left unchanged. Returns None if the
    memory does not exist."""

    def _update(s: Session) -> Optional[Memory]:
        memory = s.get(Memory, memory_id)
        if memory is None:
            return None
        if content is not None:
            memory.content = content
        if category is not None:
            memory.category = category.strip()[:32] or None
        if importance is not None:
            memory.importance = max(1, min(10, int(importance)))
        if status is not None:
            memory.status = status
        return memory

    if session is not None:
        return _update(session)
    with get_session() as s:
        return _update(s)


def archive_memory(
    memory_id: int,
    session: Optional[Session] = None,
) -> Optional[Memory]:
    """Soft-delete a memory by archiving it (kept in the DB, excluded from
    searches). Returns None if the memory does not exist."""

    def _archive(s: Session) -> Optional[Memory]:
        memory = s.get(Memory, memory_id)
        if memory is None:
            return None
        memory.status = MemoryStatus.ARCHIVED
        return memory

    if session is not None:
        return _archive(session)
    with get_session() as s:
        return _archive(s)


def list_memories(
    status: Optional[MemoryStatus] = None,
    category: Optional[str] = None,
    limit: int = 100,
    session: Optional[Session] = None,
) -> list[Memory]:
    """List memories, newest first, optionally filtered by status/category."""

    def _list(s: Session) -> list[Memory]:
        stmt = (
            select(Memory)
            .order_by(Memory.created_at.desc(), Memory.id.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(Memory.status == status)
        if category is not None:
            stmt = stmt.where(Memory.category == category)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def keyword_search_memories(
    query: str,
    status: Optional[MemoryStatus] = None,
    limit: int = 5,
    session: Optional[Session] = None,
) -> list[Memory]:
    """Simple LIKE fallback search when embeddings are unavailable."""

    def _search(s: Session) -> list[Memory]:
        pattern = f"%{query.strip()}%"
        stmt = (
            select(Memory)
            .where(Memory.content.ilike(pattern))
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(Memory.status == status)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _search(session)
    with get_session() as s:
        return _search(s)


def count_memories(
    status: Optional[MemoryStatus] = None,
    session: Optional[Session] = None,
) -> int:
    """Number of memories, optionally filtered by status."""

    def _count(s: Session) -> int:
        stmt = select(func.count()).select_from(Memory)
        if status is not None:
            stmt = stmt.where(Memory.status == status)
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)


# ---------------------------------------------------------------------------
# memory_embeddings
# ---------------------------------------------------------------------------

def save_memory_embedding(
    memory_id: int,
    model_name: str,
    embedding: np.ndarray | bytes,
    session: Optional[Session] = None,
) -> MemoryEmbedding:
    """Insert (or replace) the embedding for a memory under a model name."""

    def _save(s: Session) -> MemoryEmbedding:
        blob = embedding if isinstance(embedding, bytes) else encode_embedding(embedding)
        row = s.get(MemoryEmbedding, (memory_id,))
        if row is None:
            row = MemoryEmbedding(
                memory_id=memory_id, model_name=model_name, embedding=blob
            )
            s.add(row)
        else:
            row.model_name = model_name
            row.embedding = blob
        s.flush()
        return row

    if session is not None:
        return _save(session)
    with get_session() as s:
        return _save(s)


def list_memory_embeddings(
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> list[MemoryEmbedding]:
    """All stored memory embeddings, optionally filtered by model."""

    def _list(s: Session) -> list[MemoryEmbedding]:
        stmt = select(MemoryEmbedding)
        if model_name is not None:
            stmt = stmt.where(MemoryEmbedding.model_name == model_name)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def delete_memory_embeddings(
    memory_id: Optional[int] = None,
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> int:
    """Delete memory embeddings, filtered by memory and/or model."""

    def _delete(s: Session) -> int:
        stmt = delete(MemoryEmbedding)
        if memory_id is not None:
            stmt = stmt.where(MemoryEmbedding.memory_id == memory_id)
        if model_name is not None:
            stmt = stmt.where(MemoryEmbedding.model_name == model_name)
        result = s.execute(stmt)
        if isinstance(result, CursorResult):
            return int(result.rowcount or 0)
        return 0

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)


def count_memory_embeddings(
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> int:
    """Number of stored memory embeddings, optionally filtered by model."""

    def _count(s: Session) -> int:
        stmt = select(func.count()).select_from(MemoryEmbedding)
        if model_name is not None:
            stmt = stmt.where(MemoryEmbedding.model_name == model_name)
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)
