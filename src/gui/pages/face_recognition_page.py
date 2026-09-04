"""Developer-console page for local InsightFace recognition and enrollment."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import customtkinter as ctk
from PIL import Image, ImageDraw

from src.services.face_recognition import FaceMatch, FaceRecognitionEvent, FaceRecognitionService

logger = logging.getLogger(__name__)


class FaceRecognitionPage(ctk.CTkFrame):
    """Camera preview and one-person face-enrollment workflow."""

    def __init__(self, master, face_service: FaceRecognitionService, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.face_service = face_service
        self.camera_service = face_service.camera_service
        self._is_active = False
        self._is_destroyed = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[Image.Image] = None
        self._preview_image: Optional[ctk.CTkImage] = None
        self._latest_faces: tuple[FaceMatch, ...] = ()
        self._preview_after_id = None

        # The page is the UI adapter for the service. The service itself never
        # calls Tk directly; every callback below marshals through after_idle.
        self.face_service.on_event = self._on_service_event
        self.face_service.on_progress = self._on_progress
        self.face_service.on_name_required = self._on_name_required
        self.face_service.on_saved = self._on_saved
        self.face_service.on_error = self._on_error
        self.camera_service.add_preview_listener(self._on_camera_frame)

        self._create_layout()
        self._preview_after_id = self.after(100, self._render_preview)

    def _create_layout(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#1E1E2E")
        header.pack(fill="x", padx=15, pady=(12, 5))
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", padx=15, pady=8)
        ctk.CTkLabel(
            title_box,
            text="👤 Face Recognition",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#F5E0DC",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="Local InsightFace detection and named enrollment",
            font=ctk.CTkFont(size=11),
            text_color="#BAC2DE",
        ).pack(anchor="w")

        self.toggle_button = ctk.CTkButton(
            header,
            text="📷 Start Recognition",
            width=170,
            height=34,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#89B4FA",
            hover_color="#B4BEFE",
            text_color="#11111B",
            command=self._toggle_recognition,
        )
        self.toggle_button.pack(side="right", padx=15, pady=12)

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=15, pady=5)
        self.content.columnconfigure(0, weight=3)
        self.content.columnconfigure(1, weight=2)
        self.content.rowconfigure(0, weight=1)

        preview_card = ctk.CTkFrame(self.content, corner_radius=10, fg_color="#181825")
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.video_label = ctk.CTkLabel(
            preview_card,
            text="📷 Camera is off\n\nOpen this page to activate recognition.",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#6C7086",
        )
        self.video_label.pack(fill="both", expand=True, padx=10, pady=10)

        status_card = ctk.CTkFrame(self.content, corner_radius=10, fg_color="#181825")
        status_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(
            status_card,
            text="Recognition Status",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#89B4FA",
        ).pack(anchor="w", padx=15, pady=(15, 8))
        self.status_label = ctk.CTkLabel(
            status_card,
            text="Idle",
            font=ctk.CTkFont(size=13),
            text_color="#CDD6F4",
            wraplength=280,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=15, pady=5)
        self.progress_label = ctk.CTkLabel(
            status_card,
            text="",
            font=ctk.CTkFont(size=12),
            text_color="#A6E3A1",
        )
        self.progress_label.pack(anchor="w", padx=15, pady=5)

        self.name_frame = ctk.CTkFrame(status_card, fg_color="#1E1E2E", corner_radius=8)
        ctk.CTkLabel(
            self.name_frame,
            text="What's your name?",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#F9E2AF",
        ).pack(anchor="w", padx=10, pady=(10, 4))
        self.name_entry = ctk.CTkEntry(
            self.name_frame,
            placeholder_text="Enter a name",
            height=32,
            font=ctk.CTkFont(size=12),
        )
        self.name_entry.pack(fill="x", padx=10, pady=4)
        self.name_entry.bind("<Return>", lambda _event: self._submit_name())
        button_row = ctk.CTkFrame(self.name_frame, fg_color="transparent")
        button_row.pack(fill="x", padx=10, pady=(2, 10))
        ctk.CTkButton(
            button_row,
            text="Save Person",
            height=30,
            fg_color="#A6E3A1",
            hover_color="#94E2D5",
            text_color="#11111B",
            command=self._submit_name,
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(
            button_row,
            text="Cancel",
            height=30,
            fg_color="#F38BA8",
            hover_color="#E78284",
            text_color="#11111B",
            command=self._cancel_enrollment,
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        self.footer_label = ctk.CTkLabel(
            status_card,
            text="No raw camera frames are saved.",
            font=ctk.CTkFont(size=11),
            text_color="#6C7086",
            wraplength=280,
            justify="left",
        )
        self.footer_label.pack(anchor="w", padx=15, pady=(15, 10))

    def on_activate(self) -> None:
        self._is_active = True
        if not self.face_service.is_running:
            self._set_status("Starting camera and InsightFace...")
            if not self.face_service.start():
                self._set_status("Unable to start face recognition.")
        self._update_toggle_button()

    def on_deactivate(self) -> None:
        self._is_active = False
        if self.face_service.is_running:
            self.face_service.stop()
        self._hide_name_prompt()
        self._update_toggle_button()

    def _toggle_recognition(self) -> None:
        if self.face_service.is_running:
            self.face_service.stop()
            self._set_status("Recognition stopped.")
        elif self.face_service.start():
            self._set_status("Recognition active; looking for a face...")
        else:
            self._set_status("Unable to start face recognition.")
        self._update_toggle_button()

    def _on_camera_frame(self, frame: Image.Image) -> None:
        with self._frame_lock:
            self._latest_frame = frame.copy()

    def _render_preview(self) -> None:
        if self._is_destroyed:
            return
        try:
            if self._is_active and self.camera_service.is_running:
                width = max(240, self.video_label.winfo_width() - 20)
                height = max(180, self.video_label.winfo_height() - 20)
                aspect = self.camera_service.width / max(1, self.camera_service.height)
                target_width = width
                target_height = int(width / aspect)
                if target_height > height:
                    target_height = height
                    target_width = int(height * aspect)
                frame = self.camera_service.get_latest_frame_pil()
                if frame is None:
                    with self._frame_lock:
                        frame = self._latest_frame
                if frame is not None:
                    source_width, source_height = frame.size
                    frame = frame.resize(
                        (target_width, target_height), Image.Resampling.BILINEAR
                    )
                    self._draw_face_overlays(
                        frame,
                        source_width=source_width,
                        source_height=source_height,
                    )
                    self._preview_image = ctk.CTkImage(
                        light_image=frame,
                        dark_image=frame,
                        size=(target_width, target_height),
                    )
                    self.video_label.configure(image=self._preview_image, text="")
            elif self.video_label.cget("text") == "":
                self.video_label.configure(image="", text="📷 Camera is off")
        except Exception:
            logger.debug("Face preview render failed", exc_info=True)
        finally:
            if not self._is_destroyed and self.winfo_exists():
                self._preview_after_id = self.after(100, self._render_preview)

    def _draw_face_overlays(
        self, frame: Image.Image, *, source_width: int, source_height: int
    ) -> None:
        """Draw recognition boxes and labels on the UI preview image."""
        if not self._latest_faces:
            return
        draw = ImageDraw.Draw(frame)
        scale_x = frame.width / max(1, source_width)
        scale_y = frame.height / max(1, source_height)
        for result in self._latest_faces:
            left, top, right, bottom = result.bbox
            box = (
                int(left * scale_x),
                int(top * scale_y),
                int(right * scale_x),
                int(bottom * scale_y),
            )
            color = "#A6E3A1" if result.state == "recognized" else "#F9E2AF"
            label = result.name or "Unknown"
            if result.similarity is not None:
                label = f"{label} {result.similarity:.2f}"
            draw.rounded_rectangle(box, radius=5, outline=color, width=3)
            text_box = draw.textbbox((0, 0), label)
            text_width = text_box[2] - text_box[0]
            text_height = text_box[3] - text_box[1]
            label_left = max(0, box[0])
            label_top = max(0, box[1] - text_height - 8)
            draw.rounded_rectangle(
                (
                    label_left,
                    label_top,
                    label_left + text_width + 8,
                    label_top + text_height + 6,
                ),
                radius=3,
                fill="#11111B",
                outline=color,
            )
            draw.text(
                (label_left + 4, label_top + 3),
                label,
                fill=color,
            )

    def _on_service_event(self, event: FaceRecognitionEvent) -> None:
        self._dispatch_ui(lambda: self._apply_event(event))

    def _apply_event(self, event: FaceRecognitionEvent) -> None:
        if event.faces or event.state in {"active", "recognized", "unknown"}:
            self._latest_faces = event.faces
        enrollment_state = self.face_service.state
        if event.state == "recognized" and event.name and enrollment_state not in {"collecting", "awaiting_name", "saving"}:
            recognized = [face for face in event.faces if face.state == "recognized"]
            if recognized:
                self._set_status(
                    "\n".join(
                        f"{face.name}: {face.similarity:.2f}"
                        for face in recognized
                        if face.name and face.similarity is not None
                    )
                )
            else:
                self._set_status(f"Recognized: {event.name} ({event.similarity:.2f})")
        elif event.message and enrollment_state not in {"collecting", "awaiting_name", "saving"}:
            self._set_status(event.message)
        if event.state != "awaiting_name" and event.state != "saving":
            if event.state in {"active", "recognized", "unknown"}:
                if enrollment_state not in {"collecting", "awaiting_name", "saving"}:
                    self._hide_name_prompt()
        self._update_toggle_button()

    def _on_progress(self, count: int, target: int) -> None:
        self._dispatch_ui(lambda: self.progress_label.configure(text=f"Good frames: {count} / {target}"))

    def _on_name_required(self) -> None:
        self._dispatch_ui(self._show_name_prompt)

    def _on_saved(self, name: str) -> None:
        self._dispatch_ui(lambda: self._finish_save(name))

    def _finish_save(self, name: str) -> None:
        self._hide_name_prompt()
        self.progress_label.configure(text="")
        self._set_status(f"Saved {name}. Recognition cache updated.")

    def _on_error(self, message: str) -> None:
        self._dispatch_ui(lambda: self._apply_error(message))

    def _apply_error(self, message: str) -> None:
        self._set_status(message)
        self.name_entry.configure(state="normal")
        self._update_toggle_button()

    def _show_name_prompt(self) -> None:
        if not self.name_frame.winfo_ismapped():
            self.name_frame.pack(fill="x", padx=10, pady=(20, 5), before=self.footer_label)
        self.name_entry.focus_set()

    def _hide_name_prompt(self) -> None:
        if self.name_frame.winfo_ismapped():
            self.name_frame.pack_forget()

    def _submit_name(self) -> None:
        if self.face_service.submit_name(self.name_entry.get()):
            self.name_entry.configure(state="disabled")
            self._set_status("Saving person embeddings...")

    def _cancel_enrollment(self) -> None:
        self.face_service.cancel_enrollment()
        self.name_entry.configure(state="normal")
        self.name_entry.delete(0, "end")
        self._hide_name_prompt()
        self.progress_label.configure(text="")

    def _set_status(self, text: str) -> None:
        if self.winfo_exists():
            self.status_label.configure(text=text)

    def _update_toggle_button(self) -> None:
        if not self.winfo_exists():
            return
        if self.face_service.is_running:
            self.toggle_button.configure(text="⏹ Stop Recognition", fg_color="#F38BA8", hover_color="#E78284")
        else:
            self.toggle_button.configure(text="📷 Start Recognition", fg_color="#89B4FA", hover_color="#B4BEFE")

    def _dispatch_ui(self, callback) -> None:
        if self._is_destroyed:
            return
        try:
            self.after_idle(callback)
        except Exception:
            logger.debug("Could not dispatch face UI update", exc_info=True)

    def destroy(self) -> None:
        self._is_destroyed = True
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        try:
            self.camera_service.remove_preview_listener(self._on_camera_frame)
        except Exception:
            pass
        self.face_service.stop()
        super().destroy()
