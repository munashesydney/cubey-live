"""
Cubeo Robot Face Screen module.
High-performance 2x Super-Sampled PIL Anti-Aliased Graphics Engine rendering silky smooth,
expressive EMO-style robot eyes with Bezier crescent arcs, slanted trapezoid morphs,
and 3D perspective transformations.
"""

import math
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk, ImageOps
from typing import Callable, Optional, Tuple, List

from src.gui.screens.animations import EyeAnimationEngine

class RobotFaceScreen(ctk.CTkFrame):
    """Screen displaying Cubeo's animated OLED robot face with 2x SSAA anti-aliased Pillow rendering."""

    def __init__(self, master, on_open_developer_console: Optional[Callable[[], None]] = None, **kwargs):
        super().__init__(master, fg_color="#0A0A0F", corner_radius=0, **kwargs)
        self.on_open_developer_console = on_open_developer_console

        # Base expressive EMO eye dimensions (Large & Prominent)
        self.base_eye_width = 160
        self.base_eye_height = 180

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

        # Developer Console button in bottom-right corner
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
        delay_ms = int(random.uniform(2500, 4800))
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
        """Render ultra-fast, smooth PIL image of EMO eyes onto canvas at native resolution."""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 10 or h <= 10:
            return

        # Create PIL native-resolution canvas with OLED pitch black background
        pil_img = Image.new("RGBA", (w, h), (10, 10, 15, 255))

        # Calculate responsive eye base scale
        base_scale = min(w / 1104.0, h / 631.0)
        base_scale = max(0.5, base_scale)

        # Gaze and jitter offsets
        gaze_x = (self.animation_engine.gaze_x + self.animation_engine.jitter_offset_x)
        gaze_y = (self.animation_engine.gaze_y + self.animation_engine.jitter_offset_y)

        left_cx = (w * 0.35) + gaze_x
        right_cx = (w * 0.65) + gaze_x
        cy = (h * 0.50) + gaze_y

        color = self.animation_engine.current_color
        core_color = self.animation_engine.current_core_color
        shape_mode = self.animation_engine.shape_mode

        # Dimensions Left Eye
        l_w = (
            self.base_eye_width
            * base_scale
            * self.animation_engine.width_scale_mult
            * self.animation_engine.squish_l_x
        )
        l_h = max(
            4.0,
            self.base_eye_height
            * base_scale
            * self.animation_engine.height_scale_mult
            * self.animation_engine.squish_l_y_effective
            * self.animation_engine.blink_scale_l,
        )

        # Dimensions Right Eye
        r_w = (
            self.base_eye_width
            * base_scale
            * self.animation_engine.width_scale_mult
            * self.animation_engine.squish_r_x
        )
        r_h = max(
            4.0,
            self.base_eye_height
            * base_scale
            * self.animation_engine.height_scale_mult
            * self.animation_engine.squish_r_y_effective
            * self.animation_engine.blink_scale_r,
        )

        # Draw Left & Right EMO Eyes
        self._render_single_emo_eye(
            pil_img=pil_img,
            cx=left_cx,
            cy=cy,
            width=l_w,
            height=l_h,
            slant_deg=self.animation_engine.slant_left,
            shear_x=self.animation_engine.shear_x,
            shape_mode=shape_mode,
            eyelid_top=self.animation_engine.eyelid_top_left,
            is_left_eye=True,
            color=color,
            core_color=core_color,
        )

        self._render_single_emo_eye(
            pil_img=pil_img,
            cx=right_cx,
            cy=cy,
            width=r_w,
            height=r_h,
            slant_deg=self.animation_engine.slant_right,
            shear_x=self.animation_engine.shear_x,
            shape_mode=shape_mode,
            eyelid_top=self.animation_engine.eyelid_top_right,
            is_left_eye=False,
            color=color,
            core_color=core_color,
        )

        # Update Tkinter canvas image efficiently
        self._tk_img = ImageTk.PhotoImage(pil_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._tk_img, anchor="nw")

    def _render_single_emo_eye(
        self,
        pil_img: Image.Image,
        cx: float,
        cy: float,
        width: float,
        height: float,
        slant_deg: float,
        shear_x: float,
        shape_mode: str,
        eyelid_top: float,
        is_left_eye: bool,
        color: str,
        core_color: str,
    ) -> None:
        """Render individual EMO eye on isolated canvas, transform (rotate/shear), and composite onto main image."""
        pad = int(max(width, height) * 0.8) + 40
        canvas_w = int(width + pad)
        canvas_h = int(height + pad)

        eye_img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(eye_img)

        ecx = canvas_w / 2.0
        ecy = canvas_h / 2.0
        hw = width / 2.0
        hh = height / 2.0

        if shape_mode == "crescent_happy":
            # Image 2 style: Happy curved crescent arc eyes (^ ^)
            self._draw_crescent_arc_eye(draw, ecx, ecy, width, height, is_left_eye, color)

        elif shape_mode == "trapezoid_slant":
            # Image 1 style: Slanted angry / determined / hurt trapezoid eyes (\ /)
            self._draw_trapezoid_slant_eye(draw, ecx, ecy, width, height, is_left_eye, slant_deg, color, core_color)

        elif shape_mode == "wide_oval":
            # Image 3 style: Wide excited / surprised oval eyes (O O)
            draw.ellipse([ecx - hw, ecy - hh, ecx + hw, ecy + hh], fill=color)
            if height > width * 0.3:
                pr = min(hw, hh) * 0.42
                draw.ellipse([ecx - pr, ecy - pr, ecx + pr, ecy + pr], fill=core_color)

        else:
            # Base EMO capsule squircle
            radius = min(hw, hh) * 0.65
            draw.rounded_rectangle([ecx - hw, ecy - hh, ecx + hw, ecy + hh], radius=radius, fill=color)

            # Circular black pupil accent
            if height > width * 0.35:
                pr = min(hw, hh) * 0.38
                draw.ellipse([ecx - pr, ecy - pr, ecx + pr, ecy + pr], fill=core_color)

        # Top eyelid clipping mask if applicable
        if eyelid_top > 0.01:
            lid_h = height * eyelid_top
            draw.rectangle([ecx - hw - 20, ecy - hh - 20, ecx + hw + 20, ecy - hh + lid_h], fill=(0, 0, 0, 0))

        # Rotate transform for 3D perspective slant
        if abs(slant_deg) > 0.1:
            eye_img = eye_img.rotate(-slant_deg, resample=Image.Resampling.BICUBIC, center=(ecx, ecy))

        # Paste rendered transformed eye onto main canvas
        px = int(cx - ecx)
        py = int(cy - ecy)
        pil_img.paste(eye_img, (px, py), eye_img)

    def _draw_crescent_arc_eye(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        w: float,
        h: float,
        is_left: bool,
        color: str
    ) -> None:
        """Draw smooth, rounded horizontal crescent arc smile eye (^ ^)."""
        hw = w / 2.0
        # Arch height and line thickness for authentic horizontal EMO crescent eye
        arch_height = max(15.0, h * 0.7)
        thickness = max(14.0, h * 0.42)

        steps = 32
        points = []
        for i in range(steps + 1):
            t = i / steps
            x = cx - hw + (t * w)
            # Smooth sine inverted smile arc
            y = (cy + arch_height * 0.35) - math.sin(t * math.pi) * arch_height
            points.append((x, y))

        # Draw thick smooth stroke curve
        draw.line(points, fill=color, width=int(thickness), joint="curve")

        # Add rounded joints at EVERY point to patch Pillow's thick-line rendering cracks
        r = thickness / 2.0
        for px, py in points:
            draw.ellipse([px - r, py - r, px + r, py + r], fill=color)

    def _draw_trapezoid_slant_eye(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        w: float,
        h: float,
        is_left: bool,
        slant_deg: float,
        color: str,
        core_color: str
    ) -> None:
        r"""Draw slanted rounded trapezoid wedge eye (\ /)."""
        hw = w / 2.0
        hh = h / 2.0

        # Asymmetric wedge polygon points
        if is_left:
            # Top-left higher, top-right lower
            p1 = (cx - hw, cy - hh * 0.75)
            p2 = (cx + hw, cy - hh * 0.15)
            p3 = (cx + hw * 0.85, cy + hh * 0.75)
            p4 = (cx - hw * 0.85, cy + hh * 0.75)
        else:
            # Top-left lower, top-right higher
            p1 = (cx - hw, cy - hh * 0.15)
            p2 = (cx + hw, cy - hh * 0.75)
            p3 = (cx + hw * 0.85, cy + hh * 0.75)
            p4 = (cx - hw * 0.85, cy + hh * 0.75)

        draw.polygon([p1, p2, p3, p4], fill=color)

        # Smooth rounded corner joints at vertices
        r_corner = min(w, h) * 0.15
        for px, py in [p1, p2, p3, p4]:
            draw.ellipse([px - r_corner, py - r_corner, px + r_corner, py + r_corner], fill=color)

        # Center pupil accent
        if h > w * 0.4:
            pr = min(hw, hh) * 0.35
            draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=core_color)

    def trigger_reaction(self, reaction_type: str) -> None:
        """Trigger an emotional eye reaction using EyeAnimationEngine."""
        self.animation_engine.trigger_reaction(reaction_type, self.after)
