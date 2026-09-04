"""Local face detection, recognition, and one-person enrollment service.

The service deliberately owns no GUI objects. Camera callbacks only enqueue a
bounded number of frames; InsightFace inference and database writes happen on
worker threads so neither the camera capture thread nor Tk's event loop can be
blocked by model work.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from dataclasses import replace
from typing import Callable, Optional

import numpy as np
from PIL import Image

from src.camera.service import CameraService
from src.config import AppConfig
from src.db import (
    create_person_with_embeddings,
    clear_all_people,
    decode_face_embedding,
    list_people_with_embeddings,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceRecognitionEvent:
    """One result emitted by the background face-analysis worker."""

    state: str
    name: Optional[str] = None
    similarity: Optional[float] = None
    detection_score: Optional[float] = None
    message: str = ""
    faces: tuple["FaceMatch", ...] = ()


@dataclass(frozen=True)
class FaceMatch:
    """Recognition result and source-frame bounding box for one detected face."""

    bbox: tuple[float, float, float, float]
    state: str
    name: Optional[str] = None
    similarity: Optional[float] = None
    detection_score: Optional[float] = None


class FaceRecognitionService:
    """Run InsightFace locally and enroll unknown people on demand."""

    _UNKNOWN_STABILITY_FRAMES = 3
    _QUEUE_SIZE = 2

    def __init__(
        self,
        config: AppConfig,
        camera_service: CameraService,
        *,
        on_event: Optional[Callable[[FaceRecognitionEvent], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_name_required: Optional[Callable[[], None]] = None,
        on_enrollment_ready: Optional[Callable[[], None]] = None,
        on_saved: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.config = config
        self.camera_service = camera_service
        self.on_event = on_event
        self.on_progress = on_progress
        self.on_name_required = on_name_required
        self.on_enrollment_ready = on_enrollment_ready
        self.on_saved = on_saved
        self.on_error = on_error

        self._frames: queue.Queue[Image.Image] = queue.Queue(maxsize=self._QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._owns_camera = False
        self._analysis_paused = False
        self._live_active = False
        self._analyzer = None
        self._analyzer_error_reported = False
        self._clear_lock = threading.Lock()
        self._known_vectors: list[tuple[str, str, np.ndarray]] = []

        self._state = "idle"
        self._enrollment_embeddings: list[np.ndarray] = []
        self._enrollment_quality: list[float] = []
        self._unknown_anchor: Optional[np.ndarray] = None
        self._enrollment_last_bbox: Optional[np.ndarray] = None
        self._unknown_streak = 0
        self._last_sample_at = 0.0
        self._enrollment_started_at = 0.0

    @property
    def is_running(self) -> bool:
        """Return whether the face-analysis worker is active."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> str:
        """Return the current enrollment/recognition state."""
        with self._state_lock:
            return self._state

    @property
    def enrollment_count(self) -> int:
        with self._state_lock:
            return len(self._enrollment_embeddings)

    @property
    def is_analysis_paused(self) -> bool:
        """Return whether camera frames are temporarily excluded from analysis."""
        with self._state_lock:
            return self._analysis_paused

    @property
    def is_live_active(self) -> bool:
        """Return whether Gemini Live currently owns the background face mode."""
        with self._state_lock:
            return self._live_active

    def set_live_active(self, active: bool) -> None:
        """Coordinate service lifetime with the logical Gemini Live session."""
        with self._state_lock:
            self._live_active = bool(active)

    def set_analysis_paused(self, paused: bool) -> None:
        """Pause or resume analysis while leaving the shared camera running."""
        with self._state_lock:
            self._analysis_paused = bool(paused)
        if not paused:
            self._drain_frame_queue()
        logger.info("Face recognition analysis %s.", "paused" if paused else "resumed")

    def start(self) -> bool:
        """Start camera analysis and load known people into memory."""
        if self.is_running:
            return True

        if not self.camera_service.is_running:
            self._owns_camera = True
            # CameraService initializes its capture worker asynchronously; do
            # not hold the GUI thread waiting for hardware probing here.
            self.camera_service.start(wait_ready=False)
        else:
            self._owns_camera = False

        self._stop_event.clear()
        self._reset_enrollment_state()
        with self._state_lock:
            self._state = "active"
            self._analysis_paused = False
        self.camera_service.add_preview_listener(self._on_camera_frame)
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="FaceRecognitionWorker",
        )
        self._thread.start()
        self._emit(FaceRecognitionEvent(state="active", message="Face recognition active."))
        return True

    def stop(self, *, force: bool = False) -> None:
        """Stop analysis, discard temporary enrollment data, and release owned camera."""
        if self.is_live_active and not force:
            logger.info("Keeping face recognition active for the Gemini Live session.")
            return
        self._stop_event.set()
        try:
            self.camera_service.remove_preview_listener(self._on_camera_frame)
        except Exception:
            logger.debug("Could not unregister face camera listener", exc_info=True)
        try:
            self._frames.put_nowait(Image.new("RGB", (1, 1)))
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._reset_enrollment_state()
        with self._state_lock:
            self._state = "idle"
            self._analysis_paused = False
        if self._owns_camera:
            self.camera_service.stop()
        self._owns_camera = False

    def submit_name(self, name: str) -> bool:
        """Persist the completed enrollment without blocking the GUI thread."""
        prepared = self._prepare_enrollment_save(name)
        if prepared is None:
            return False
        display_name, embeddings, quality = prepared

        threading.Thread(
            target=self._save_enrollment,
            args=(display_name, embeddings, quality),
            daemon=True,
            name="FaceEnrollmentSaveWorker",
        ).start()
        self._emit(FaceRecognitionEvent(state="saving", message="Saving face embeddings..."))
        return True

    def save_pending_enrollment(self, name: str) -> tuple[bool, str]:
        """Synchronously save the pending enrollment for an AI tool call.

        Tool dispatch runs this method on a worker thread, so the database write
        and model-cache refresh do not block Gemini Live's realtime event loop.
        """
        prepared = self._prepare_enrollment_save(name)
        if prepared is None:
            with self._state_lock:
                state = self._state
            if state != "awaiting_name":
                return False, "There is no face enrollment waiting for a name."
            return False, "Please provide a non-empty name before saving the face."
        display_name, embeddings, quality = prepared
        self._emit(FaceRecognitionEvent(state="saving", message="Saving face embeddings..."))
        return self._save_enrollment(display_name, embeddings, quality)

    def _prepare_enrollment_save(
        self, name: str
    ) -> Optional[tuple[str, list[np.ndarray], list[float]]]:
        display_name = " ".join(str(name or "").strip().split())
        with self._state_lock:
            if self._state != "awaiting_name":
                return None
            if not display_name:
                self._report_error("Please enter a name before saving.")
                return None
            embeddings = [vector.copy() for vector in self._enrollment_embeddings]
            quality = list(self._enrollment_quality)
            self._state = "saving"
        return display_name, embeddings, quality

    def cancel_enrollment(self) -> None:
        """Discard the current temporary enrollment and resume recognition."""
        with self._state_lock:
            if self._state in {"collecting", "awaiting_name"}:
                self._reset_enrollment_state()
                self._state = "active"
        self._emit(FaceRecognitionEvent(state="active", message="Enrollment cancelled."))

    def clear_all_faces(self) -> bool:
        """Delete all persisted faces and clear the in-memory recognition cache."""
        if not self._clear_lock.acquire(blocking=False):
            return False
        with self._state_lock:
            if self._state == "saving":
                self._clear_lock.release()
                self._report_error("Please wait for the current person to finish saving.")
                return False
            self._state = "clearing" if self.is_running else "idle"
        self._emit(FaceRecognitionEvent(state="clearing", message="Clearing all saved faces..."))
        threading.Thread(
            target=self._clear_all_faces_worker,
            daemon=True,
            name="FaceClearWorker",
        ).start()
        return True

    def _clear_all_faces_worker(self) -> None:
        try:
            people_deleted, embeddings_deleted = clear_all_people()
            with self._state_lock:
                self._known_vectors.clear()
                self._reset_enrollment_state()
                self._state = "active" if self.is_running else "idle"
                state = self._state
            self._emit(
                FaceRecognitionEvent(
                    state=state,
                    message=(
                        f"Cleared {people_deleted} people and "
                        f"{embeddings_deleted} face embeddings."
                    ),
                )
            )
        except Exception as exc:
            self._report_error(f"Could not clear saved faces: {exc}")
        finally:
            self._clear_lock.release()

    def _on_camera_frame(self, frame: Image.Image) -> None:
        """Enqueue the newest frame without allowing backpressure on capture."""
        if self.is_analysis_paused:
            return
        try:
            self._frames.put_nowait(frame.copy())
        except queue.Full:
            try:
                self._frames.get_nowait()
                self._frames.put_nowait(frame.copy())
            except queue.Empty:
                pass

    def _worker(self) -> None:
        try:
            # Model loading may download a model pack on first use, so it must
            # never happen on Tk's UI thread.
            self._load_known_people()
            self._get_analyzer()
        except Exception as exc:
            self._report_error(f"InsightFace could not start: {exc}")
            try:
                self.camera_service.remove_preview_listener(self._on_camera_frame)
            except Exception:
                pass
            if self._owns_camera:
                self.camera_service.stop()
            with self._state_lock:
                self._state = "error"
            return

        interval = 1.0 / max(0.5, float(self.config.face_analysis_fps))
        next_analysis = 0.0
        while not self._stop_event.is_set():
            try:
                frame = self._frames.get(timeout=0.25)
            except queue.Empty:
                self._check_enrollment_timeout()
                continue
            now = time.monotonic()
            if self.is_analysis_paused:
                continue
            if now < next_analysis:
                continue
            next_analysis = now + interval
            try:
                self._analyze_frame(frame, now)
            except Exception:
                logger.exception("Face analysis failed for a camera frame")
                self._report_error("Face analysis failed; recognition is still running.")

    def _get_analyzer(self):
        if self._analyzer is not None:
            return self._analyzer
        try:
            from insightface.app import FaceAnalysis
        except ImportError as exc:
            raise RuntimeError(
                "insightface is not installed. Install the cubeyLive requirements."
            ) from exc
        detector_size = max(160, int(self.config.face_detection_size))
        analyzer = FaceAnalysis(
            name=self.config.face_model_name,
            providers=["CPUExecutionProvider"],
        )
        analyzer.prepare(ctx_id=0, det_size=(detector_size, detector_size))
        self._analyzer = analyzer
        return analyzer

    def _load_known_people(self) -> None:
        people = list_people_with_embeddings(model_name=self.config.face_model_name)
        vectors: list[tuple[str, str, np.ndarray]] = []
        for person in people:
            for sample in person.embeddings:
                vector = decode_face_embedding(sample.embedding, sample.dimension)
                vector = self._normalize(vector)
                if np.linalg.norm(vector) > 0:
                    vectors.append((str(person.id), person.name, vector))
        with self._state_lock:
            self._known_vectors = vectors
        logger.info("Loaded %d face embeddings for %d people.", len(vectors), len(people))

    def _analyze_frame(self, frame: Image.Image, now: float) -> None:
        with self._state_lock:
            state = self._state
        if state in {"awaiting_name", "saving", "clearing"}:
            return

        rgb = np.asarray(frame.convert("RGB"), dtype=np.uint8)
        bgr = rgb[:, :, ::-1]
        faces = self._get_analyzer().get(bgr)
        if not faces:
            self._unknown_streak = 0
            self._check_enrollment_timeout(now)
            self._emit(FaceRecognitionEvent(state="active", message="No face detected."))
            return

        results: list[FaceMatch] = []
        unknown_candidates: list[tuple[object, np.ndarray, float]] = []
        for face in faces:
            face_bbox = np.asarray(face.bbox, dtype=np.float32).reshape(4)
            embedding = self._normalize(np.asarray(face.embedding, dtype=np.float32))
            detection_score = float(getattr(face, "det_score", 0.0) or 0.0)
            if detection_score < self.config.face_detection_confidence:
                continue
            if self._face_width(face) < self.config.face_minimum_size:
                continue

            match_name, similarity = self._match(embedding)
            if match_name is not None:
                results.append(
                    FaceMatch(
                        bbox=tuple(float(value) for value in face_bbox),
                        state="recognized",
                        name=match_name,
                        similarity=similarity,
                        detection_score=detection_score,
                    )
                )
            else:
                results.append(
                    FaceMatch(
                        bbox=tuple(float(value) for value in face_bbox),
                        state="unknown",
                        similarity=similarity,
                        detection_score=detection_score,
                    )
                )
                unknown_candidates.append((face, embedding, detection_score))

        if not results:
            self._unknown_streak = 0
            self._check_enrollment_timeout(now)
            self._emit(FaceRecognitionEvent(state="active", message="No good face detected."))
            return

        # Enrollment intentionally remains single-person: use the largest
        # unknown face while still reporting every face to the UI.
        if unknown_candidates:
            face, embedding, detection_score = max(
                unknown_candidates, key=lambda item: self._face_area(item[0])
            )
            enrollment_bbox = tuple(
                float(value)
                for value in np.asarray(face.bbox, dtype=np.float32).reshape(4)
            )
            self._handle_unknown(
                embedding,
                detection_score,
                now,
                np.asarray(face.bbox, dtype=np.float32).reshape(4),
                emit_event=False,
            )
            if self.state in {"collecting", "awaiting_name", "saving"}:
                results = [
                    replace(result, state="enrolling")
                    if result.state == "unknown" and result.bbox == enrollment_bbox
                    else result
                    for result in results
                ]
        else:
            self._unknown_streak = 0

        recognized = [result for result in results if result.state == "recognized"]
        names = ", ".join(
            f"{result.name} ({result.similarity:.2f})"
            for result in recognized
            if result.name and result.similarity is not None
        )
        state = "recognized" if recognized else "unknown"
        self._emit(
            FaceRecognitionEvent(
                state=state,
                name=recognized[0].name if recognized else None,
                similarity=recognized[0].similarity if recognized else None,
                detection_score=results[0].detection_score,
                message=(f"Recognized: {names}" if names else f"{len(results)} unknown face(s) detected."),
                faces=tuple(results),
            )
        )

    def _handle_unknown(
        self,
        embedding: np.ndarray,
        quality: float,
        now: float,
        bbox: Optional[np.ndarray] = None,
        *,
        emit_event: bool = True,
    ) -> None:
        with self._state_lock:
            state = self._state
        if state == "idle":
            return
        if state == "active":
            if self._unknown_anchor is None or self._cosine(self._unknown_anchor, embedding) < self.config.face_enrollment_consistency_threshold:
                self._unknown_anchor = embedding.copy()
                self._unknown_streak = 1
            else:
                self._unknown_streak += 1
            if emit_event:
                self._emit(FaceRecognitionEvent(state="unknown", message="Unknown face detected."))
            if self._unknown_streak >= self._UNKNOWN_STABILITY_FRAMES:
                with self._state_lock:
                    self._state = "collecting"
                self._enrollment_started_at = now
                self._enrollment_embeddings.clear()
                self._enrollment_quality.clear()
                self._enrollment_last_bbox = bbox.copy() if bbox is not None else None
                self._append_enrollment(embedding, quality, now, bbox)
                return
        elif state == "collecting":
            if self._same_enrollment_face(embedding, bbox):
                self._append_enrollment(embedding, quality, now, bbox)
        self._check_enrollment_timeout(now)

    def _append_enrollment(
        self,
        embedding: np.ndarray,
        quality: float,
        now: float,
        bbox: Optional[np.ndarray] = None,
    ) -> None:
        if now - self._last_sample_at < 0.18:
            return
        target = max(1, int(self.config.face_enrollment_target_frames))
        with self._state_lock:
            if self._state != "collecting":
                return
            self._enrollment_embeddings.append(embedding.copy())
            self._enrollment_quality.append(quality)
            count = len(self._enrollment_embeddings)
            # A running centroid accommodates ordinary pose and expression
            # changes while retaining a stable identity anchor.
            self._unknown_anchor = self._normalize(
                np.mean(self._enrollment_embeddings, axis=0)
            )
            if bbox is not None:
                self._enrollment_last_bbox = bbox.copy()
        self._last_sample_at = now
        if self.on_progress:
            self.on_progress(count, target)
        self._emit(FaceRecognitionEvent(state="collecting", message=f"Collecting {count} / {target} good frames."))
        if count >= target:
            with self._state_lock:
                self._state = "awaiting_name"
            self._emit(FaceRecognitionEvent(state="awaiting_name", message="What's your name?"))
            if self.on_name_required:
                self.on_name_required()
            if self.on_enrollment_ready:
                try:
                    self.on_enrollment_ready()
                except Exception:
                    logger.debug("Face enrollment-ready callback failed", exc_info=True)

    def _same_enrollment_face(
        self, embedding: np.ndarray, bbox: Optional[np.ndarray]
    ) -> bool:
        """Accept normal pose movement without accepting an unrelated face."""
        if self._unknown_anchor is None:
            return True
        similarity = self._cosine(self._unknown_anchor, embedding)
        if similarity >= self.config.face_enrollment_min_similarity:
            return True
        if bbox is None or self._enrollment_last_bbox is None:
            return False
        return self._bbox_continuity(self._enrollment_last_bbox, bbox)

    @staticmethod
    def _bbox_continuity(previous: np.ndarray, current: np.ndarray) -> bool:
        """Check that the tracked face moved plausibly between samples."""
        prev = np.asarray(previous, dtype=np.float32)
        curr = np.asarray(current, dtype=np.float32)
        prev_w, prev_h = max(1.0, prev[2] - prev[0]), max(1.0, prev[3] - prev[1])
        curr_w, curr_h = max(1.0, curr[2] - curr[0]), max(1.0, curr[3] - curr[1])
        prev_center = np.array([(prev[0] + prev[2]) / 2, (prev[1] + prev[3]) / 2])
        curr_center = np.array([(curr[0] + curr[2]) / 2, (curr[1] + curr[3]) / 2])
        center_distance = float(np.linalg.norm(curr_center - prev_center))
        size_ratio = min(prev_w / curr_w, curr_w / prev_w, prev_h / curr_h, curr_h / prev_h)
        return center_distance <= max(prev_w, prev_h) * 0.75 and size_ratio >= 0.40

    def _save_enrollment(
        self, name: str, embeddings: list[np.ndarray], quality: list[float]
    ) -> tuple[bool, str]:
        try:
            if not embeddings:
                raise ValueError("No enrollment embeddings were collected.")
            create_person_with_embeddings(
                name,
                embeddings,
                model_name=self.config.face_model_name,
                dimension=int(embeddings[0].size),
                quality_scores=quality,
            )
            self._load_known_people()
            with self._state_lock:
                self._reset_enrollment_state()
                self._state = "active"
            message = f"Saved {name}."
            self._emit(FaceRecognitionEvent(state="active", name=name, message=message))
            if self.on_saved:
                self.on_saved(name)
            return True, message
        except Exception as exc:
            with self._state_lock:
                self._state = "awaiting_name"
            self._report_error(str(exc))
            return False, str(exc)

    def _match(self, embedding: np.ndarray) -> tuple[Optional[str], Optional[float]]:
        with self._state_lock:
            known = list(self._known_vectors)
        if not known:
            return None, None
        best_person: Optional[str] = None
        best_score = -1.0
        for _person_id, name, stored in known:
            score = self._cosine(embedding, stored)
            if score > best_score:
                best_person, best_score = name, score
        if best_score >= self.config.face_match_threshold:
            return best_person, best_score
        return None, best_score

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector

    @staticmethod
    def _cosine(left: np.ndarray, right: np.ndarray) -> float:
        return float(np.dot(left, right))

    @staticmethod
    def _face_area(face) -> float:
        bbox = np.asarray(face.bbox, dtype=np.float32)
        return max(0.0, float(bbox[2] - bbox[0])) * max(0.0, float(bbox[3] - bbox[1]))

    @staticmethod
    def _face_width(face) -> float:
        bbox = np.asarray(face.bbox, dtype=np.float32)
        return max(0.0, float(bbox[2] - bbox[0]))

    def _check_enrollment_timeout(self, now: Optional[float] = None) -> None:
        with self._state_lock:
            if self._state != "collecting":
                return
        current = now if now is not None else time.monotonic()
        if current - self._enrollment_started_at <= self.config.face_enrollment_timeout_seconds:
            return
        self._reset_enrollment_state()
        with self._state_lock:
            self._state = "active"
        self._report_error("Enrollment timed out; please look at the camera and try again.")

    def _reset_enrollment_state(self) -> None:
        with self._state_lock:
            self._enrollment_embeddings.clear()
            self._enrollment_quality.clear()
            self._unknown_anchor = None
            self._enrollment_last_bbox = None
            self._unknown_streak = 0
            self._last_sample_at = 0.0
            self._enrollment_started_at = 0.0

    def _drain_frame_queue(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def _emit(self, event: FaceRecognitionEvent) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                logger.debug("Face-recognition event callback failed", exc_info=True)

    def _report_error(self, message: str) -> None:
        logger.warning("Face recognition: %s", message)
        self._emit(FaceRecognitionEvent(state="error", message=message))
        if self.on_error:
            self.on_error(message)
