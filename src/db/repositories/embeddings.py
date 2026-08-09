"""
CRUD operations for the message_embeddings table.

Embeddings are stored as raw float32 little-endian bytes (see encode/decode
helpers). Same session contract as the other repositories.
"""

import logging
from typing import Optional

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from src.db.models import MessageEmbedding
from src.db.session import get_session

logger = logging.getLogger(__name__)


def encode_embedding(vector: np.ndarray) -> bytes:
    """Serialize a float vector to raw float32 bytes for storage."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def decode_embedding(blob: bytes) -> np.ndarray:
    """Deserialize stored float32 bytes back into a numpy array."""
    return np.frombuffer(blob, dtype=np.float32)


def save_message_embedding(
    message_id: int,
    model_name: str,
    embedding: np.ndarray | bytes,
    session: Optional[Session] = None,
) -> MessageEmbedding:
    """Insert (or replace) the embedding for a message under a model name."""

    def _save(s: Session) -> MessageEmbedding:
        blob = embedding if isinstance(embedding, bytes) else encode_embedding(embedding)
        row = s.get(MessageEmbedding, (message_id,))
        if row is None:
            row = MessageEmbedding(
                message_id=message_id, model_name=model_name, embedding=blob
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


def get_message_embedding(
    message_id: int,
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> Optional[MessageEmbedding]:
    """Fetch the embedding for a message, optionally restricted to a model."""

    def _get(s: Session) -> Optional[MessageEmbedding]:
        if model_name is None:
            return s.get(MessageEmbedding, (message_id,))
        stmt = select(MessageEmbedding).where(
            MessageEmbedding.message_id == message_id,
            MessageEmbedding.model_name == model_name,
        )
        return s.execute(stmt).scalar_one_or_none()

    if session is not None:
        return _get(session)
    with get_session() as s:
        return _get(s)


def list_embeddings(
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> list[MessageEmbedding]:
    """All stored embeddings, optionally filtered by embedding model."""

    def _list(s: Session) -> list[MessageEmbedding]:
        stmt = select(MessageEmbedding)
        if model_name is not None:
            stmt = stmt.where(MessageEmbedding.model_name == model_name)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def delete_message_embeddings(
    message_id: Optional[int] = None,
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> int:
    """Delete embeddings, filtered by message and/or model. Returns rows removed."""

    def _delete(s: Session) -> int:
        stmt = delete(MessageEmbedding)
        if message_id is not None:
            stmt = stmt.where(MessageEmbedding.message_id == message_id)
        if model_name is not None:
            stmt = stmt.where(MessageEmbedding.model_name == model_name)
        result = s.execute(stmt)
        if isinstance(result, CursorResult):
            return int(result.rowcount or 0)
        return 0

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)


def count_embeddings(
    model_name: Optional[str] = None,
    session: Optional[Session] = None,
) -> int:
    """Number of stored embeddings, optionally filtered by model."""

    def _count(s: Session) -> int:
        stmt = select(func.count()).select_from(MessageEmbedding)
        if model_name is not None:
            stmt = stmt.where(MessageEmbedding.model_name == model_name)
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)
