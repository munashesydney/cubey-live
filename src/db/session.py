"""
Session management helpers for Cubey.

`get_session` is the recommended way to run a unit of work: it opens a
short-lived session, commits on success, and rolls back on error. Repository
functions use this internally, and callers can pass an explicit session to
compose multiple operations into a single transaction.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.orm import Session

from src.db.base import SessionLocal

logger = logging.getLogger(__name__)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a short-lived session; commit on success, roll back on error."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
