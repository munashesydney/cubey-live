"""CRUD and bulk persistence helpers for face-recognition data."""

import logging
import unicodedata
from typing import Iterable, Optional

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from src.db.models import Person, PersonEmbedding
from src.db.session import get_session

logger = logging.getLogger(__name__)


def normalize_person_name(name: str) -> str:
    """Return a stable, case-insensitive key for a display name."""
    return " ".join(unicodedata.normalize("NFKC", name).strip().casefold().split())


def encode_face_embedding(vector: np.ndarray | bytes) -> bytes:
    """Serialize a face vector as contiguous little-endian float32 bytes."""
    if isinstance(vector, bytes):
        return vector
    return np.ascontiguousarray(vector, dtype="<f4").tobytes()


def decode_face_embedding(blob: bytes, dimension: Optional[int] = None) -> np.ndarray:
    """Deserialize a stored face vector and validate its optional dimension."""
    vector = np.frombuffer(blob, dtype="<f4")
    if dimension is not None and vector.size != dimension:
        raise ValueError(
            f"Stored face embedding has dimension {vector.size}; expected {dimension}."
        )
    return vector.copy()


def create_person_with_embeddings(
    name: str,
    embeddings: Iterable[np.ndarray | bytes],
    *,
    model_name: str,
    dimension: int,
    quality_scores: Optional[Iterable[float | None]] = None,
    session: Optional[Session] = None,
) -> Person:
    """Atomically create a named person and all of their enrollment samples."""
    display_name = " ".join(unicodedata.normalize("NFKC", name).strip().split())
    normalized_name = normalize_person_name(display_name)
    if not normalized_name:
        raise ValueError("Person name cannot be empty.")
    if len(display_name) > 120:
        raise ValueError("Person name cannot exceed 120 characters.")

    vectors = list(embeddings)
    if not vectors:
        raise ValueError("At least one face embedding is required.")
    scores = list(quality_scores) if quality_scores is not None else [None] * len(vectors)
    if len(scores) != len(vectors):
        raise ValueError("quality_scores must contain one value per embedding.")

    def _create(s: Session) -> Person:
        existing = s.execute(
            select(Person).where(Person.normalized_name == normalized_name)
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError(f"A person named {display_name!r} already exists.")

        person = Person(name=display_name, normalized_name=normalized_name)
        s.add(person)
        s.flush()
        for vector, score in zip(vectors, scores):
            blob = encode_face_embedding(vector)
            if len(blob) != dimension * 4:
                raise ValueError("Face embedding byte length does not match dimension.")
            s.add(
                PersonEmbedding(
                    person_id=person.id,
                    model_name=model_name,
                    dimension=dimension,
                    embedding=blob,
                    quality_score=score,
                )
            )
        s.flush()
        return person

    if session is not None:
        return _create(session)
    with get_session() as s:
        return _create(s)


def list_people_with_embeddings(
    *, model_name: Optional[str] = None, session: Optional[Session] = None
) -> list[Person]:
    """Load people and their embeddings for the in-memory matcher."""

    def _list(s: Session) -> list[Person]:
        stmt = select(Person).options(joinedload(Person.embeddings)).order_by(Person.id)
        people = list(s.execute(stmt).unique().scalars().all())
        if model_name is not None:
            for person in people:
                person.embeddings = [
                    sample for sample in person.embeddings if sample.model_name == model_name
                ]
            people = [person for person in people if person.embeddings]
        return people

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def list_people(session: Optional[Session] = None) -> list[Person]:
    """Return all known people without requiring their vector payloads."""

    def _list(s: Session) -> list[Person]:
        return list(s.scalars(select(Person).order_by(Person.name)).all())

    if session is not None:
        return _list(session)
    with get_session() as s:
        return _list(s)


def delete_person(person_id: int, session: Optional[Session] = None) -> bool:
    """Delete a person and their embeddings."""

    def _delete(s: Session) -> bool:
        person = s.get(Person, person_id)
        if person is None:
            return False
        s.delete(person)
        return True

    if session is not None:
        return _delete(session)
    with get_session() as s:
        return _delete(s)
