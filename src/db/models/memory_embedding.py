"""
Memory embedding ORM model — semantic vectors for durable memories.

Mirrors message_embeddings: one row per memory per embedding model, stored as
raw float32 bytes so vectors from different models are never mixed.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class MemoryEmbedding(Base):
    """One embedding vector per memory (per embedding model)."""

    __tablename__ = "memory_embeddings"

    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), primary_key=True
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
            f"<MemoryEmbedding memory_id={self.memory_id} "
            f"model={self.model_name!r} bytes={len(self.embedding)}>"
        )
