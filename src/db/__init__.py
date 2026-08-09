"""
Cubey database package.

Callers can reach every operation through this package, e.g.:

    from src.db import create_conversation, end_conversation, list_conversations
    from src.db import get_session, Conversation, save_message_embedding

Repository functions open and commit their own short-lived session unless an
explicit session is passed in, so they are safe to call from any thread.
"""

from src.db.base import Base, SessionLocal, engine
from src.db.models import (
    Conversation,
    ConversationStatus,
    Message,
    MessageEmbedding,
    MessageRole,
)
from src.db.repositories import (
    count_conversations,
    count_embeddings,
    count_messages,
    create_conversation,
    create_message,
    decode_embedding,
    delete_conversation,
    delete_message_embeddings,
    encode_embedding,
    end_conversation,
    get_conversation,
    get_message,
    get_message_embedding,
    list_conversations,
    list_embeddings,
    list_messages,
    save_message_embedding,
    update_conversation,
)
from src.db.session import get_session

__all__ = [
    "Base",
    "Conversation",
    "ConversationStatus",
    "Message",
    "MessageEmbedding",
    "MessageRole",
    "SessionLocal",
    "count_conversations",
    "count_embeddings",
    "count_messages",
    "create_conversation",
    "create_message",
    "decode_embedding",
    "delete_conversation",
    "delete_message_embeddings",
    "encode_embedding",
    "end_conversation",
    "engine",
    "get_conversation",
    "get_message",
    "get_message_embedding",
    "get_session",
    "list_conversations",
    "list_embeddings",
    "list_messages",
    "save_message_embedding",
    "update_conversation",
]
