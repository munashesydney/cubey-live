"""
Minimalist Apple-style startup loading screen for Cubey.
Pure monochrome OLED aesthetic with a clean 'CUBEY' logo and a sleek, thin loading bar.
"""

import logging
import math
from typing import Callable, Optional
import customtkinter as ctk

logger = logging.getLogger(__name__)


class StartupPage(ctk.CTkFrame):
    """Sleek minimalist Apple-inspired startup screen."""

    def __init__(
        self,
        master: any,
        on_startup_complete: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        # Pure deep black OLED background
        super().__init__(master, fg_color="#000000", corner_radius=0, **kwargs)
        self.on_startup_complete = on_startup_complete

        self._progress = 0.0
        self._status_text = "Starting up..."
        self._is_completed = False
        self._pulse_angle = 0.0
        self._pulse_after_id: Optional[str] = None

        self._build_ui()
        self._start_breathing_animation()

    def _build_ui(self) -> None:
        """Construct clean, minimalist monochrome layout."""
        # Center container
        self.center_box = ctk.CTkFrame(self, fg_color="transparent")
        self.center_box.place(relx=0.5, rely=0.5, anchor="center")

        # Big, elegant CUBEY logo in crisp white
        self.title_label = ctk.CTkLabel(
            self.center_box,
            text="C U B E Y",
            font=ctk.CTkFont(family="Trebuchet MS", size=58, weight="bold"),
            text_color="#FFFFFF",
        )
        self.title_label.pack(pady=(0, 36))

        # Thin, elegant Apple-style progress bar
        self.progress_bar = ctk.CTkProgressBar(
            self.center_box,
            width=260,
            height=4,
            corner_radius=2,
            progress_color="#FFFFFF",
            fg_color="#242426",
        )
        self.progress_bar.set(0.0)
        self.progress_bar.pack(pady=(0, 16))

        # Subtle, muted status text below the loading bar
        self.status_label = ctk.CTkLabel(
            self.center_box,
            text=self._status_text,
            font=ctk.CTkFont(family="Helvetica", size=11),
            text_color="#86868B",
        )
        self.status_label.pack()

    def _start_breathing_animation(self) -> None:
        """Subtle smooth breathing luminosity on the title."""
        if self._is_completed or not self.winfo_exists():
            return

        self._pulse_angle = (self._pulse_angle + 0.06) % (2 * math.pi)
        luminosity = 0.88 + 0.12 * math.sin(self._pulse_angle)
        val = int(255 * luminosity)
        hex_col = f"#{val:02x}{val:02x}{val:02x}"

        try:
            self.title_label.configure(text_color=hex_col)
            self._pulse_after_id = self.after(50, self._start_breathing_animation)
        except Exception:
            pass

    def destroy(self) -> None:
        """Cancel animation timer on widget destruction."""
        self._is_completed = True
        if self._pulse_after_id:
            try:
                self.after_cancel(self._pulse_after_id)
            except Exception:
                pass
            self._pulse_after_id = None
        super().destroy()

    def set_progress(
        self,
        progress: float,
        status_text: str = "",
        active_step_index: Optional[int] = None,
    ) -> None:
        """Update progress bar value and optional status message."""
        self._progress = max(0.0, min(1.0, float(progress)))
        if status_text:
            self._status_text = status_text

        try:
            self.progress_bar.set(self._progress)
            if status_text:
                self.status_label.configure(text=status_text)
        except Exception as e:
            logger.debug("Failed updating startup progress UI: %s", e)

    def complete(self) -> None:
        """Mark startup complete and trigger transition to Robot Face."""
        if self._is_completed:
            return
        self._is_completed = True

        self.set_progress(1.0, "")
        logger.info("Startup complete. Transitioning to Robot Face...")
        # Brief elegant pause
        self.after(300, self._trigger_completion_callback)

    def _trigger_completion_callback(self) -> None:
        """Invoke external callback to transition views."""
        if self.on_startup_complete:
            try:
                self.on_startup_complete()
            except Exception as e:
                logger.error("Error in on_startup_complete callback: %s", e, exc_info=True)
