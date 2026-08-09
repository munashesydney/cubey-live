"""
Cubey database package.

Callers can reach every operation through this package, e.g.:

    from src.db import create_conversation, end_conversation, list_conversations
    from src.db import get_session, Conversation

Repository functions open and commit their own short-lived session unless an
explicit session is passed in, so they are safe to call from any thread.
"""

from src.db.base import Base, SessionLocal, engine
from src.db.models import Conversation, ConversationStatus
from src.db.repositories import (
    count_conversations,
    create_conversation,
    delete_conversation,
    end_conversation,
    get_conversation,
    list_conversations,
    update_conversation,
)
from src.db.session import get_session

__all__ = [
    "Base",
    "Conversation",
    "ConversationStatus",
    "SessionLocal",
    "count_conversations",
    "create_conversation",
    "delete_conversation",
    "end_conversation",
    "engine",
    "get_conversation",
    "get_session",
    "list_conversations",
    "update_conversation",
]
