"""
Message embedding ORM model — semantic vectors for past messages.

Embeddings are derived artifacts tied to a specific model (`model_name`), so
vectors from different models are never mixed. Stored as raw float32 bytes
(1.5 KB for 384 dims) rather than JSON to keep the scan light.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class MessageEmbedding(Base):
    """One embedding vector per message (per embedding model)."""

    __tablename__ = "message_embeddings"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    model_name: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<MessageEmbedding message_id={self.message_id} "
            f"model={self.model_name!r} bytes={len(self.embedding)}>"
        )
