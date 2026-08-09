"""
Memory ORM model — durable facts about the user/world, distinct from raw
conversation messages. Each row is one fact the AI chose to remember.
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class MemoryStatus(str, enum.Enum):
    """Lifecycle states of a memory."""

    ACTIVE = "active"
    ARCHIVED = "archived"

    @staticmethod
    def values(enum_class) -> list[str]:
        """The persisted lowercase values, for SQLAlchemy's values_callable."""
        return [member.value for member in enum_class]


class Memory(Base):
    """A single durable fact remembered about the user or the world."""

    __tablename__ = "memories"

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_memories_status"),
        CheckConstraint("importance BETWEEN 1 AND 10", name="ck_memories_importance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    importance: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=5
    )
    status: Mapped[MemoryStatus] = mapped_column(
        SqlEnum(
            MemoryStatus,
            values_callable=MemoryStatus.values,
            native_enum=False,
            length=16,
        ),
        index=True,
        nullable=False,
        default=MemoryStatus.ACTIVE,
    )
    # Provenance: which message/conversation this memory came from (nullable —
    # the AI may add memories directly without a specific source).
    source_message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    source_conversation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return f"<Memory id={self.id} status={self.status.value!r} content={self.content[:40]!r}>"
