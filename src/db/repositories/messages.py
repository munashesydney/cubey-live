"""
CRUD operations for the messages table.

Same contract as the conversations repository: each call opens and commits its
own short-lived session unless an explicit `session` is passed in.
"""

import logging
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import Message, MessageRole
from src.db.session import get_session

logger = logging.getLogger(__name__)


def _coerce_role(role: MessageRole | str) -> MessageRole:
    """Accept a MessageRole member or a valid raw string."""
    if isinstance(role, MessageRole):
        return role
    return MessageRole(role)


def create_message(
    conversation_id: int,
    role: MessageRole | str,
    content: str,
    session: Optional[Session] = None,
) -> Message:
    """Persist one transcript line. Raises if conversation_id does not exist
    (foreign key) or role is not a valid MessageRole value."""

    def _create(s: Session) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=_coerce_role(role),
            content=content,
        )
        s.add(message)
        s.flush()
        return message

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def get_message(
    message_id: int,
    session: Optional[Session] = None,
) -> Optional[Message]:
    """Fetch a single message by primary key."""

    def _get(s: Session) -> Optional[Message]:
        return s.get(Message, message_id)

    if session is not None:
        return _get(session)
    with get_session() as s:
        return _get(s)


def list_messages(
    conversation_id: int,
    limit: int = 500,
    session: Optional[Session] = None,
) -> list[Message]:
    """Messages for a conversation in chronological order."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    def _list(s: Session) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        return list(s.scalars(stmt).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def count_messages(
    conversation_id: int,
    session: Optional[Session] = None,
) -> int:
    """Number of messages in a conversation."""

    def _count(s: Session) -> int:
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return int(s.execute(stmt).scalar_one())

    if session is not None:
        return _count(session)
    with get_session() as s:
        return _count(s)
