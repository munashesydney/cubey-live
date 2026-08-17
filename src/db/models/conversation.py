"""
Conversation ORM model — one row per interactive session between user and Cubey.
"""

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class ConversationStatus(str, enum.Enum):
    """Lifecycle states of a conversation."""

    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"

    @staticmethod
    def values(enum_class) -> list[str]:
        """The persisted lowercase values, for SQLAlchemy's values_callable."""
        return [member.value for member in enum_class]


class ConversationSource(str, enum.Enum):
    """The chat experience that owns a conversation."""

    GEMINI = "gemini"
    LOCAL = "local"

    @staticmethod
    def values(enum_class) -> list[str]:
        """The persisted lowercase values, for SQLAlchemy's values_callable."""
        return [member.value for member in enum_class]


class Conversation(Base):
    """A single interactive session between the user and Cubey."""

    __tablename__ = "conversations"

    # DB-level domain enforcement on top of the ORM enum.
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'completed', 'archived')",
            name="ck_conversations_status",
        ),
        CheckConstraint(
            "source IN ('gemini', 'local')",
            name="ck_conversations_source",
        ),
        # Matches the conversation-history query: source + newest first.
        Index("ix_conversations_source_started_at", "source", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[ConversationStatus] = mapped_column(
        SqlEnum(
            ConversationStatus,
            values_callable=ConversationStatus.values,
            native_enum=False,
            length=16,
        ),
        index=True,
        nullable=False,
        default=ConversationStatus.ACTIVE,
    )
    source: Mapped[ConversationSource] = mapped_column(
        SqlEnum(
            ConversationSource,
            values_callable=ConversationSource.values,
            native_enum=False,
            length=16,
        ),
        nullable=False,
        default=ConversationSource.GEMINI,
    )
    # Attribute is metadata_json because "metadata" is reserved by the
    # Declarative API; the underlying column remains named "metadata".
    metadata_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} session_id={self.session_id!r} "
            f"status={self.status!r}>"
        )
