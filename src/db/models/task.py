"""
Task ORM model — scheduled jobs that run an AI pipeline when due.

Schedules are one of:
  - one_shot: run once at `run_at`
  - interval: run every `interval_seconds`
  - cron:     run per `cron_expr` (standard 5-field cron, machine-local time)

`next_run_at` (UTC) is what the scheduler polls; it is recomputed after each
run. One-shot tasks transition to DONE after running.
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, utcnow


class TaskStatus(str, enum.Enum):
    """Lifecycle states of a task."""

    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"

    @staticmethod
    def values(enum_class) -> list[str]:
        return [member.value for member in enum_class]


class TaskScheduleType(str, enum.Enum):
    """How a task's next run is determined."""

    ONE_SHOT = "one_shot"
    INTERVAL = "interval"
    CRON = "cron"

    @staticmethod
    def values(enum_class) -> list[str]:
        return [member.value for member in enum_class]


class TaskModel(str, enum.Enum):
    """Which AI runs the task when it is due."""

    LOCAL = "local"
    GEMINI = "gemini"

    @staticmethod
    def values(enum_class) -> list[str]:
        return [member.value for member in enum_class]


class Task(Base):
    """A scheduled job that runs an AI pipeline when due."""

    __tablename__ = "tasks"

    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'done')", name="ck_tasks_status"),
        CheckConstraint(
            "schedule_type IN ('one_shot', 'interval', 'cron')",
            name="ck_tasks_schedule_type",
        ),
        CheckConstraint("model IN ('local', 'gemini')", name="ck_tasks_model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[TaskModel] = mapped_column(
        SqlEnum(
            TaskModel, values_callable=TaskModel.values, native_enum=False, length=16
        ),
        index=True,
        nullable=False,
    )
    schedule_type: Mapped[TaskScheduleType] = mapped_column(
        SqlEnum(
            TaskScheduleType,
            values_callable=TaskScheduleType.values,
            native_enum=False,
            length=16,
        ),
        nullable=False,
    )
    # one_shot target (UTC), interval length in seconds, or cron expression.
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    interval_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cron_expr: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[TaskStatus] = mapped_column(
        SqlEnum(
            TaskStatus, values_callable=TaskStatus.values, native_enum=False, length=16
        ),
        index=True,
        nullable=False,
        default=TaskStatus.ACTIVE,
    )

    # Outcome of the most recent run.
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:
        return (
            f"<Task id={self.id} model={self.model.value!r} "
            f"type={self.schedule_type.value!r} status={self.status.value!r}>"
        )
