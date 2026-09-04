"""People known to Cubey's local face-recognition subsystem."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, utcnow

if TYPE_CHECKING:
    from src.db.models.person_embedding import PersonEmbedding


class Person(Base):
    """A named person with one or more face-embedding samples."""

    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_people_normalized_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    embeddings: Mapped[list["PersonEmbedding"]] = relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PersonEmbedding.id",
    )

    def __repr__(self) -> str:
        return f"<Person id={self.id} name={self.name!r}>"
