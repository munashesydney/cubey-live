"""Persisted InsightFace embedding samples for known people."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, utcnow

if TYPE_CHECKING:
    from src.db.models.person import Person


class PersonEmbedding(Base):
    """One normalized face vector captured during a person's enrollment."""

    __tablename__ = "person_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[int] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    person: Mapped["Person"] = relationship(back_populates="embeddings")

    def __repr__(self) -> str:
        return (
            f"<PersonEmbedding id={self.id} person_id={self.person_id} "
            f"model={self.model_name!r} dimension={self.dimension}>"
        )
