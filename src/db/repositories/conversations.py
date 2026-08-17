"""
CRUD operations for the conversations table.

Safe to call from anywhere in the codebase (GUI thread, asyncio worker thread,
scripts, tests). Each call opens and commits its own short-lived session unless
an explicit `session` is passed in, in which case the caller owns the
transaction — pass a session to compose multiple operations atomically.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Conversation, ConversationSource, ConversationStatus
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_conversation(
    session_id: str,
    title: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    source: ConversationSource = ConversationSource.GEMINI,
    session: Optional[Session] = None,
) -> Conversation:
    """Create a new conversation in ACTIVE status.

    `metadata` is an arbitrary JSON payload stored in the "metadata" column
    (exposed on the model as `metadata_json`).
    """

    def _create(s: Session) -> Conversation:
        conversation = Conversation(
            session_id=session_id,
            title=title,
            metadata_json=metadata,
            source=source,
        )
        s.add(conversation)
        s.flush()
        return conversation

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def get_conversation(
    conversation_id: Optional[int] = None,
    session_id: Optional[str] = None,
    session: Optional[Session] = None,
) -> Optional[Conversation]:
    """Fetch a conversation by primary key or by unique session_id."""
    if conversation_id is None and session_id is None:
        raise ValueError("Provide either conversation_id or session_id")

    def _get(s: Session) -> Optional[Conversation]:
        if conversation_id is not None:
            return s.get(Conversation, conversation_id)
        stmt = select(Conversation).where(Conversation.session_id == session_id)
        return s.execute(stmt).scalar_one_or_none()

    if session is not None:
        return _get(session)
    with get_session() as s:
        return _get(s)


def list_conversations(
    status: Optional[ConversationStatus] = None,
    source: Optional[ConversationSource] = None,
    limit: int = 50,
    offset: int = 0,
    session: Optional[Session] = None,
) -> list[Conversation]:
    """List conversations, newest first, optionally filtered by status and source."""
    if limit < 1 or offset < 0:
        raise ValueError("limit must be >= 1 and offset >= 0")

    def _list(s: Session) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .order_by(Conversation.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status is not None:
            stmt = stmt.where(Conversation.status == status)
        if source is not None:
            stmt = stmt.where(Conversation.source == source)
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def update_conversation(
    conversation_id: int,
    *,
    title: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    status: Optional[ConversationStatus] = None,
    ended_at: Optional[datetime] = None,
    session: Optional[Session] = None,
) -> Optional[Conversation]:
    """
    Update a conversation. Any argument left as None is left unchanged, so a
    value cannot be cleared to NULL through this helper. Returns the updated
    row, or None if the conversation does not exist.
    """

    def _update(s: Session) -> Optional[Conversation]:
        conversation = s.get(Conversation, conversation_id)
        if conversation is None:
            return None
        if title is not None:
            conversation.title = title
        if metadata is not None:
            conversation.metadata_json = metadata
        if status is not None:
            conversation.status = status
        if ended_at is not None:
            conversation.ended_at = ended_at
        return conversation

    if session is not None:
        return _update(session)
    with get_session() as s:
        return _update(s)


def end_conversation(
    conversation_id: int,
    session: Optional[Session] = None,
) -> Optional[Conversation]:
    """Mark a conversation COMPLETED and stamp ended_at (idempotent)."""

    def _end(s: Session) -> Optional[Conversation]:
        conversation = s.get(Conversation, conversation_id)
        if conversation is None:
            return None
        conversation.status = ConversationStatus.COMPLETED
        if conversation.ended_at is None:
            conversation.ended_at = _utcnow()
        return conversation

    if session is not None:
        return _end(session)
    with get_session() as s:
        return _end(s)


def delete_conversation(conversation_id: int, session: Optional[Session] = None) -> bool:
    """Hard-delete a conversation. Returns True if a row was removed."""
    # For soft delete, use update_conversation(status=ConversationStatus.ARCHIVED).

    def _delete(s: Session) -> bool:
        conversation = s.get(Conversation, conversation_id)
        if conversation is None:
            return False
        s.delete(conversation)
        return True

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)


def count_conversations(
    status: Optional[ConversationStatus] = None,
    session: Optional[Session] = None,
) -> int:
    """Count conversations, optionally filtered by status."""

    def _count(s: Session) -> int:
        stmt = select(func.count()).select_from(Conversation)
        if status is not None:
            stmt = stmt.where(Conversation.status == status)
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)
