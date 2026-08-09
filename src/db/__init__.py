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
    Memory,
    MemoryEmbedding,
    MemoryStatus,
    Message,
    MessageEmbedding,
    MessageRole,
)
from src.db.repositories import (
    archive_memory,
    count_conversations,
    count_embeddings,
    count_memories,
    count_memory_embeddings,
    count_messages,
    create_conversation,
    create_memory,
    create_message,
    decode_embedding,
    delete_conversation,
    delete_memory_embeddings,
    delete_message_embeddings,
    encode_embedding,
    end_conversation,
    get_conversation,
    get_memory,
    get_message,
    get_message_embedding,
    keyword_search_memories,
    list_conversations,
    list_embeddings,
    list_memories,
    list_memory_embeddings,
    list_messages,
    save_memory_embedding,
    save_message_embedding,
    update_conversation,
    update_memory,
)
from src.db.session import get_session

__all__ = [
    "Base",
    "Conversation",
    "ConversationStatus",
    "Memory",
    "MemoryEmbedding",
    "MemoryStatus",
    "Message",
    "MessageEmbedding",
    "MessageRole",
    "SessionLocal",
    "archive_memory",
    "count_conversations",
    "count_embeddings",
    "count_memories",
    "count_memory_embeddings",
    "count_messages",
    "create_conversation",
    "create_memory",
    "create_message",
    "decode_embedding",
    "delete_conversation",
    "delete_memory_embeddings",
    "delete_message_embeddings",
    "encode_embedding",
    "end_conversation",
    "engine",
    "get_conversation",
    "get_memory",
    "get_message",
    "get_message_embedding",
    "get_session",
    "keyword_search_memories",
    "list_conversations",
    "list_embeddings",
    "list_memories",
    "list_memory_embeddings",
    "list_messages",
    "save_memory_embedding",
    "save_message_embedding",
    "update_conversation",
    "update_memory",
]
