"""
Message ORM model — one row per transcript line within a conversation.
"""

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class MessageRole(str, enum.Enum):
    """Who produced the message within a conversation."""

    USER = "user"
    MODEL = "model"
    EVENT = "event"
    SYSTEM = "system"

    @staticmethod
    def values(enum_class) -> list[str]:
        """The persisted lowercase values, for SQLAlchemy's values_callable."""
        return [member.value for member in enum_class]


class Message(Base):
    """A single transcript line persisted against a conversation."""

    __tablename__ = "messages"

    # DB-level domain enforcement on top of the ORM enum.
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'model', 'event', 'system')",
            name="ck_messages_role",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(
        SqlEnum(
            MessageRole,
            values_callable=MessageRole.values,
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Message id={self.id} conversation_id={self.conversation_id} "
            f"role={self.role!r}>"
        )
