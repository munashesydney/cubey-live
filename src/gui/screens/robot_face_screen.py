"""
Cubeo Robot Face Screen module.
Renders 60 FPS animated glowing capsule eyes with natural random blinking and physical event reactions.
"""

import math
import random
import tkinter as tk
import customtkinter as ctk
from typing import Optional

class RobotFaceScreen(ctk.CTkFrame):
    """Screen displaying Cubeo's animated OLED robot face."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="#0A0A0F", **kwargs)

        # Eye styling parameters matching Cubeo image
        self.eye_color = "#89CFF0"       # Glowing soft cyan/light blue
        self.eye_color_core = "#E0F7FA"  # Bright white-cyan core
        self.bg_color = "#0A0A0F"
        
        # Eye geometry parameters (default full size)
        self.base_eye_width = 46
        self.base_eye_height = 135
        self.eye_corner_radius = 23      # Half of width for perfect capsule/pill ends

        # Animation states
        self.current_height_pct = 1.0    # 1.0 = fully open, 0.05 = closed blink
        self.target_height_pct = 1.0
        self.is_blinking = False
        self.blink_step = 0
        self.emotion_state = "NORMAL"
        self.reaction_timer_id: Optional[str] = None

        # Create drawing canvas
        self.canvas = tk.Canvas(
            self,
            bg=self.bg_color,
            bd=0,
            highlightthickness=0,
            relief="ridge"
        )
        self.canvas.pack(fill="both", expand=True)

        # Bind resize event to re-center eyes dynamically
        self.canvas.bind("<Configure>", self._on_resize)

        # Start random blink timer loop
        self._schedule_random_blink()
        self._animation_loop()

    def _on_resize(self, event=None) -> None:
        """Redraw face elements when canvas resizes."""
        self._draw_face()

    def _schedule_random_blink(self) -> None:
        """Schedule the next random eye blink in 2.5 to 5.0 seconds."""
        delay_ms = int(random.uniform(2500, 5000))
        self.after(delay_ms, self._start_blink)

    def _start_blink(self) -> None:
        """Initiate an eye blink animation cycle."""
        if not self.is_blinking and self.emotion_state == "NORMAL":
            self.is_blinking = True
            self.blink_step = 0

    def _animation_loop(self) -> None:
        """Main 60 FPS animation loop handling smooth height interpolation."""
        if self.is_blinking:
            # Blink cycle: 0 -> 4 steps closing, 4 -> 8 steps opening
            if self.blink_step <= 4:
                # Closing
                self.current_height_pct = max(0.05, 1.0 - (self.blink_step / 4.0) * 0.95)
            elif self.blink_step <= 8:
                # Opening
                self.current_height_pct = min(1.0, 0.05 + ((self.blink_step - 4) / 4.0) * 0.95)
            else:
                # Blink complete
                self.current_height_pct = 1.0
                self.is_blinking = False
                self._schedule_random_blink()
            
            self.blink_step += 1

        self._draw_face()
        # Schedule next frame in ~16ms (60 FPS)
        self.after(16, self._animation_loop)

    def _draw_face(self) -> None:
        """Draw Cubeo capsule eyes on the canvas."""
        self.canvas.delete("all")

        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 1 or h <= 1:
            return

        # Calculate eye centers (spaced at 38% and 62% of width)
        left_center_x = w * 0.38
        right_center_x = w * 0.62
        center_y = h * 0.50

        eye_w = self.base_eye_width
        eye_h = max(8, self.base_eye_height * self.current_height_pct)

        # Draw left eye capsule and right eye capsule
        self._draw_capsule_eye(left_center_x, center_y, eye_w, eye_h, self.eye_color)
        self._draw_capsule_eye(right_center_x, center_y, eye_w, eye_h, self.eye_color)

    def _draw_capsule_eye(self, cx: float, cy: float, width: float, height: float, color: str) -> None:
        """Draw a capsule/pill shape (rounded rectangle) centered at (cx, cy)."""
        x1 = cx - width / 2
        y1 = cy - height / 2
        x2 = cx + width / 2
        y2 = cy + height / 2

        r = min(width / 2, height / 2)  # Corner radius for capsule ends

        # Draw smooth capsule using rounded rectangle (polygon with arcs or smooth oval)
        if height <= width:
            # Flattened eye during blink
            self.canvas.create_oval(x1, y1, x2, y2, fill=color, outline="")
        else:
            # Full capsule shape (top circle, middle rectangle, bottom circle)
            self.canvas.create_oval(x1, y1, x2, y1 + width, fill=color, outline="")
            self.canvas.create_oval(x1, y2 - width, x2, y2, fill=color, outline="")
            self.canvas.create_rectangle(x1, y1 + r, x2, y2 - r, fill=color, outline="")

            # Subtle inner bright core highlight for glowing OLED look
            core_w = width * 0.4
            core_h = max(4, (height - width) * 0.5)
            self.canvas.create_rectangle(
                cx - core_w / 2,
                cy - core_h / 2,
                cx + core_w / 2,
                cy + core_h / 2,
                fill=self.eye_color_core,
                outline=""
            )

    def trigger_reaction(self, event_payload: str) -> None:
        """Trigger an emotional eye reaction when a physical event occurs."""
        self.emotion_state = "REACTION"
        original_color = self.eye_color

        if "KICKED" in event_payload or "SPILLED" in event_payload:
            self.eye_color = "#F38BA8"  # Hurt Red/Coral
        elif "OBSTACLE" in event_payload or "FRAME" in event_payload:
            self.eye_color = "#FAB387"  # Alert Orange
        elif "BATTERY" in event_payload:
            self.eye_color = "#CBA6F7"  # Low Power Purple
        else:
            self.eye_color = "#A6E3A1"  # Soft Green

        self._draw_face()

        # Reset back to normal after 1.5 seconds
        if self.reaction_timer_id:
            self.after_cancel(self.reaction_timer_id)
            
        self.reaction_timer_id = self.after(1500, lambda: self._reset_emotion(original_color))

    def _reset_emotion(self, default_color: str) -> None:
        """Reset face back to normal emotion."""
        self.eye_color = default_color
        self.emotion_state = "NORMAL"
        self._draw_face()
