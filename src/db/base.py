"""
SQLAlchemy engine, declarative base, and session factory for Cubey.

Engine-level SQLite pragmas (WAL, foreign keys, busy timeout) are applied here
so every connection from any thread inherits them. This is what makes the
database safe to hit from both the GUI thread and the asyncio worker thread.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config import config

logger = logging.getLogger(__name__)

# How long a writer waits for a locked database before raising (milliseconds).
_SQLITE_BUSY_TIMEOUT_MS = 5000


def utcnow() -> datetime:
    """Timezone-aware UTC now (SQLite stores it as a naive UTC string)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create the parent directory of a SQLite file, if any."""
    if not database_url.startswith("sqlite:///"):
        return
    db_path = database_url[len("sqlite:///"):].split("?", 1)[0]
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _apply_sqlite_pragmas(engine: Engine) -> None:
    """Set production-grade PRAGMAs on every new SQLite connection."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def create_db_engine(database_url: Optional[str] = None) -> Engine:
    """Create the application engine for the configured database URL."""
    url = database_url or config.database_url
    _ensure_sqlite_parent_dir(url)
    engine = create_engine(url, echo=False)
    if url.startswith("sqlite"):
        _apply_sqlite_pragmas(engine)
    return engine


engine = create_db_engine()

# expire_on_commit=False keeps returned ORM objects usable after their session
# is closed, so repository results survive the short-lived session pattern.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
