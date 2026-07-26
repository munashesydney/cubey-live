"""
Cubeo Robot Face Screen module.
High-performance 2x Super-Sampled PIL Anti-Aliased Graphics Engine rendering silky smooth,
commercial-grade solid white capsule eyes with perfect circular black pupils.
"""

import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
from typing import Callable, Optional

from src.gui.screens.animations import EyeAnimationEngine

class RobotFaceScreen(ctk.CTkFrame):
    """Screen displaying Cubeo's animated OLED robot face with 2x SSAA anti-aliased Pillow rendering."""

    def __init__(self, master, on_open_developer_console: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="#0A0A0F", corner_radius=0, **kwargs)
        self.on_open_developer_console = on_open_developer_console

        # Base eye dimensions
        self.base_eye_width = 46
        self.base_eye_height = 135

        # Instantiate Animation Engine
        self.animation_engine = EyeAnimationEngine(redraw_callback=self._draw_face)

        # Image cache reference for Tkinter Garbage Collector
        self._tk_img: Optional[ImageTk.PhotoImage] = None

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
        delay_ms = int(random.uniform(2800, 5200))
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
        """Render ultra-crisp 2x super-sampled anti-aliased PIL image of Cubeo eyes onto canvas."""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 10 or h <= 10:
            return

        # 2x Super-Sampling factor for silky smooth subpixel anti-aliasing
        scale = 2
        sw = w * scale
        sh = h * scale

        # Create PIL high-resolution canvas
        pil_img = Image.new("RGBA", (sw, sh), (10, 10, 15, 255))
        draw = ImageDraw.Draw(pil_img)

        # Scale eye dimensions
        base_scale = min(w / 1104.0, h / 631.0)
        base_scale = max(0.4, base_scale)

        eye_w = self.base_eye_width * base_scale * scale
        eye_h = max(
            8 * scale,
            self.base_eye_height * base_scale * scale * self.animation_engine.effective_height_scale
        )

        # Apply Micro-Gaze Saccades and Pain Jitter offsets
        gaze_x = (self.animation_engine.gaze_offset_x + self.animation_engine.jitter_offset_x) * scale
        gaze_y = (self.animation_engine.gaze_offset_y + self.animation_engine.jitter_offset_y) * scale

        left_cx = (w * 0.38 * scale) + gaze_x
        right_cx = (w * 0.62 * scale) + gaze_x
        cy = (h * 0.50 * scale) + gaze_y

        color = self.animation_engine.current_color
        core_color = self.animation_engine.current_core_color

        # Draw eyes on high-res PIL canvas
        self._draw_pil_capsule_eye(draw, left_cx, cy, eye_w, eye_h, color, core_color)
        self._draw_pil_capsule_eye(draw, right_cx, cy, eye_w, eye_h, color, core_color)

        # Downsample to target size with Lanczos anti-aliasing filter
        final_img = pil_img.resize((w, h), resample=Image.Resampling.LANCZOS)
        
        self._tk_img = ImageTk.PhotoImage(final_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._tk_img, anchor="nw")

    def _draw_pil_capsule_eye(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        width: float,
        height: float,
        color: str,
        core_color: str
    ) -> None:
        """Draw clean, borderless rounded capsule eye with circular black pupil."""
        x1 = cx - width / 2
        y1 = cy - height / 2
        x2 = cx + width / 2
        y2 = cy + height / 2

        radius = min(width / 2, height / 2)

        # Layer 1: Solid White (or emotion color) Rounded Capsule
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=color)

        # Layer 2: Perfect Circular Black Pupil
        if height > width * 0.4:
            pupil_diameter = width * 0.42
            pr = pupil_diameter / 2.0
            draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=core_color)

    def trigger_reaction(self, reaction_type: str) -> None:
        """Trigger an emotional eye reaction using EyeAnimationEngine."""
        self.animation_engine.trigger_reaction(reaction_type, self.after)
