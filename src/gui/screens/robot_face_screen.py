"""
Cubeo Robot Face Screen module.
Delegates all visual rendering, blinking, and emotional state changes to EyeAnimationEngine.
"""

import tkinter as tk
import customtkinter as ctk
from typing import Callable, Optional

from src.gui.screens.animations import EyeAnimationEngine

class RobotFaceScreen(ctk.CTkFrame):
    """Screen displaying Cubeo's animated OLED robot face."""

    def __init__(self, master, on_open_developer_console: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="#0A0A0F", corner_radius=0, **kwargs)
        self.on_open_developer_console = on_open_developer_console

        # Base eye dimensions
        self.base_eye_width = 46
        self.base_eye_height = 135

        # Instantiate Animation Engine
        self.animation_engine = EyeAnimationEngine(redraw_callback=self._draw_face)

        # Create 100% full-bleed drawing canvas
        self.canvas = tk.Canvas(
            self,
            bg="#0A0A0F",
            bd=0,
            highlightthickness=0,
            relief="ridge"
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<Configure>", self._on_resize)

        # Subtle Developer Console button in bottom-right corner
        self.dev_btn = ctk.CTkButton(
            self,
            text="⚙️ Dev",
            font=ctk.CTkFont(size=11),
            fg_color="#1E1E2E",
            hover_color="#313244",
            text_color="#BAC2DE",
            width=65,
            height=26,
            corner_radius=6,
            command=self._handle_dev_click
        )
        self.dev_btn.place(relx=0.98, rely=0.96, anchor="se")

        # Start blink timer and main 60 FPS animation loop
        self._schedule_random_blink()
        self._animation_loop()

    def _handle_dev_click(self) -> None:
        """Handler for corner developer button."""
        if self.on_open_developer_console:
            self.on_open_developer_console()

    def _on_resize(self, event=None) -> None:
        """Redraw face elements when canvas resizes."""
        self._draw_face()

    def _schedule_random_blink(self) -> None:
        """Schedule random eye blink."""
        import random
        delay_ms = int(random.uniform(2500, 5000))
        self.after(delay_ms, self._trigger_blink)

    def _trigger_blink(self) -> None:
        """Initiate blink and schedule next."""
        self.animation_engine.start_blink()
        self._schedule_random_blink()

    def _animation_loop(self) -> None:
        """Main 60 FPS animation loop advancing animation engine frame."""
        self.animation_engine.update_animation_frame()
        self.after(16, self._animation_loop)

    def _draw_face(self) -> None:
        """Draw Cubeo capsule eyes on canvas based on animation engine parameters."""
        self.canvas.delete("all")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1 or h <= 1:
            return

        left_center_x = w * 0.38
        right_center_x = w * 0.62
        center_y = h * 0.50

        scale_factor = min(w / 1104.0, h / 631.0)
        scale_factor = max(0.4, scale_factor)

        eye_w = self.base_eye_width * scale_factor
        eye_h = max(
            6,
            self.base_eye_height * scale_factor * self.animation_engine.height_pct * self.animation_engine.height_scale_mult
        )

        color = self.animation_engine.current_color
        core_color = self.animation_engine.current_core_color

        self._draw_capsule_eye(left_center_x, center_y, eye_w, eye_h, color, core_color)
        self._draw_capsule_eye(right_center_x, center_y, eye_w, eye_h, color, core_color)

    def _draw_capsule_eye(self, cx: float, cy: float, width: float, height: float, color: str, core_color: str) -> None:
        """Draw capsule shape centered at (cx, cy)."""
        x1 = cx - width / 2
        y1 = cy - height / 2
        x2 = cx + width / 2
        y2 = cy + height / 2

        r = min(width / 2, height / 2)

        if height <= width:
            self.canvas.create_oval(x1, y1, x2, y2, fill=color, outline="")
        else:
            self.canvas.create_oval(x1, y1, x2, y1 + width, fill=color, outline="")
            self.canvas.create_oval(x1, y2 - width, x2, y2, fill=color, outline="")
            self.canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=color, outline="")

            core_w = width * 0.4
            core_h = max(4, (height - width) * 0.5)
            self.canvas.create_rectangle(
                cx - core_w / 2,
                cy - core_h / 2,
                cx + core_w / 2,
                cy + core_h / 2,
                fill=core_color,
                outline=""
            )

    def trigger_reaction(self, reaction_type: str) -> None:
        """Trigger an emotional eye reaction using EyeAnimationEngine."""
        self.animation_engine.trigger_reaction(reaction_type, self.after)
