"""Tests for face-recognition persistence and enrollment state transitions."""

import unittest
from unittest.mock import MagicMock

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import AppConfig
from src.db import Base, create_person_with_embeddings, list_people_with_embeddings
from src.services.face_recognition import FaceRecognitionService


class TestPeoplePersistence(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_person_and_multiple_embeddings_are_saved(self):
        vectors = [np.array([1, 0, 0, 0], dtype=np.float32), np.array([0, 1, 0, 0], dtype=np.float32)]
        person = create_person_with_embeddings(
            "  John   Doe ",
            vectors,
            model_name="buffalo_s",
            dimension=4,
            quality_scores=[0.9, 0.8],
            session=self.session,
        )
        self.session.commit()

        people = list_people_with_embeddings(model_name="buffalo_s", session=self.session)
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].name, "John Doe")
        self.assertEqual(people[0].normalized_name, "john doe")
        self.assertEqual(len(people[0].embeddings), 2)
        self.assertEqual(people[0].embeddings[0].dimension, 4)
        self.assertEqual(person.id, people[0].id)

    def test_duplicate_names_are_case_insensitive(self):
        vector = np.ones(4, dtype=np.float32)
        create_person_with_embeddings(
            "John", [vector], model_name="buffalo_s", dimension=4, session=self.session
        )
        self.session.commit()
        with self.assertRaises(ValueError):
            create_person_with_embeddings(
                " john ", [vector], model_name="buffalo_s", dimension=4, session=self.session
            )


class TestFaceRecognitionService(unittest.TestCase):
    def setUp(self):
        self.camera = MagicMock()
        self.camera.is_running = True
        self.config = AppConfig(
            face_enrollment_target_frames=3,
            face_enrollment_consistency_threshold=0.5,
            face_enrollment_timeout_seconds=30,
        )
        self.name_required = MagicMock()
        self.service = FaceRecognitionService(
            self.config,
            self.camera,
            on_name_required=self.name_required,
        )

    def test_match_uses_cosine_similarity_and_threshold(self):
        self.service._known_vectors = [("1", "John", np.array([1, 0, 0, 0], dtype=np.float32))]
        name, score = self.service._match(np.array([1, 0, 0, 0], dtype=np.float32))
        self.assertEqual(name, "John")
        self.assertAlmostEqual(score, 1.0)
        unknown, _ = self.service._match(np.array([0, 1, 0, 0], dtype=np.float32))
        self.assertIsNone(unknown)

    def test_unknown_face_collects_samples_then_requests_name(self):
        vector = np.array([1, 0, 0, 0], dtype=np.float32)
        self.service._state = "active"
        self.service._handle_unknown(vector, 0.9, 1.0)
        self.service._handle_unknown(vector, 0.9, 1.2)
        self.service._handle_unknown(vector, 0.9, 1.4)
        self.service._handle_unknown(vector, 0.9, 1.6)
        self.service._handle_unknown(vector, 0.9, 1.8)

        self.assertEqual(self.service.state, "awaiting_name")
        self.assertEqual(self.service.enrollment_count, 3)
        self.name_required.assert_called_once()

    def test_cancel_discards_temporary_embeddings(self):
        self.service._state = "collecting"
        self.service._enrollment_embeddings.append(np.ones(4, dtype=np.float32))
        self.service.cancel_enrollment()
        self.assertEqual(self.service.state, "active")
        self.assertEqual(self.service.enrollment_count, 0)

    def test_enrollment_accepts_pose_variation_above_minimum_similarity(self):
        self.service._state = "collecting"
        self.service._unknown_anchor = np.array([1, 0, 0, 0], dtype=np.float32)
        self.service._enrollment_started_at = 1.0
        varied = np.array([0.85, 0.53, 0, 0], dtype=np.float32)
        self.service._handle_unknown(varied, 0.9, 1.0, np.array([10, 10, 110, 110]))
        self.assertEqual(self.service.enrollment_count, 1)


if __name__ == "__main__":
    unittest.main()
