"""Database repositories — one module per table."""

from src.db.repositories.conversations import (
    count_conversations,
    create_conversation,
    delete_conversation,
    end_conversation,
    get_conversation,
    list_conversations,
    update_conversation,
)
from src.db.repositories.embeddings import (
    count_embeddings,
    decode_embedding,
    delete_message_embeddings,
    encode_embedding,
    get_message_embedding,
    list_embeddings,
    save_message_embedding,
)
from src.db.repositories.messages import (
    count_messages,
    create_message,
    get_message,
    list_messages,
)

__all__ = [
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
    "get_conversation",
    "get_message",
    "get_message_embedding",
    "list_conversations",
    "list_embeddings",
    "list_messages",
    "save_message_embedding",
    "update_conversation",
]
