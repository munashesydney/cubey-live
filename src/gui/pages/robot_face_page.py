"""
Cubeo Robot Face page.
High-performance super-sampled PIL anti-aliased graphics engine rendering smooth,
expressive EMO-style robot eyes, sleep breathing animations with floating Zzz snore particles,
and sleek glowing charging HUD indicators.
"""

import logging
import math
import random
import time
import tkinter as tk
from typing import Callable, Optional

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from src.gui.pages.animations import EyeAnimationEngine

logger = logging.getLogger(__name__)


class RobotFacePage(ctk.CTkFrame):
    """Page displaying Cubeo's animated OLED robot face with 2x SSAA anti-aliased Pillow rendering."""

    def __init__(
        self,
        master,
        on_open_developer_console: Optional[Callable[[], None]] = None,
        target_fps: int = 30,
        supersampling: int = 2,
        **kwargs,
    ):
        super().__init__(master, fg_color="#0A0A0F", corner_radius=0, **kwargs)
        self.on_open_developer_console = on_open_developer_console
        self.target_fps = max(15, min(int(target_fps), 60))
        self.supersampling = max(1, min(int(supersampling), 3))
        self._frame_interval = 1.0 / self.target_fps
        self._last_frame_at = time.perf_counter()

        logger.info(
            "Robot face renderer configured for %d FPS at %dx supersampling",
            self.target_fps,
            self.supersampling,
        )

        # Base expressive EMO eye dimensions (Large & Prominent)
        self.base_eye_width = 160
        self.base_eye_height = 180

        # Instantiate Animation Engine
        self.animation_engine = EyeAnimationEngine(redraw_callback=self._draw_face)

        # Charging & Battery State
        self.is_charging = False
        self.battery_pct = 100
        self.is_sleeping = False

        # Wake Word / Gemini Live Listening State & Side Waves
        self.is_listening = False
        self.listening_intensity = 0.0
        self.current_mic_level = 0.0

        # Image cache reference for Tkinter Garbage Collector
        self._tk_img: Optional[ImageTk.PhotoImage] = None
        self._canvas_image_id: Optional[int] = None

        # Create 100% full-bleed drawing canvas
        self.canvas = tk.Canvas(
            self,
            bg="#0A0A0F",
            bd=0,
            highlightthickness=0,
            relief="ridge",
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
            command=self._handle_dev_click,
        )
        self.dev_btn.place(relx=0.98, rely=0.96, anchor="se")

        # Start blink timer and the CPU-bounded animation loop.
        self._schedule_random_blink()
        self._animation_loop()

    def set_charging(self, is_charging: bool, battery_pct: int = 0) -> None:
        """Update charging state and battery percentage."""
        self.is_charging = is_charging
        if battery_pct > 0:
            self.battery_pct = battery_pct
        self.animation_engine.set_charging(is_charging, battery_pct)

    def set_sleeping(self, is_sleeping: bool) -> None:
        """Toggle sleeping mode."""
        self.is_sleeping = is_sleeping
        self.animation_engine.set_sleeping(is_sleeping)

    def set_listening(self, is_listening: bool) -> None:
        """Toggle listening state for glowing side wave visualizer."""
        self.is_listening = is_listening
        if is_listening:
            # Wake up if sleeping when listening begins
            if self.is_sleeping:
                self.set_sleeping(False)

    def set_mic_level(self, level: float) -> None:
        """Update current audio input volume level for dynamic wave modulation."""
        self.current_mic_level = max(0.0, min(1.0, float(level)))

    def _handle_dev_click(self) -> None:
        """Handler for corner developer button."""
        if self.on_open_developer_console:
            self.on_open_developer_console()

    def _on_resize(self, event=None) -> None:
        """Redraw face elements when canvas resizes."""
        self._draw_face()

    def _schedule_random_blink(self) -> None:
        """Schedule random eye blink."""
        if self.animation_engine.is_sleeping:
            delay_ms = int(random.uniform(8000, 15000))
        else:
            delay_ms = int(random.uniform(2500, 4800))
        self.after(delay_ms, self._trigger_blink)

    def _trigger_blink(self) -> None:
        """Initiate blink and schedule next."""
        self.animation_engine.start_blink()
        self._schedule_random_blink()

    def _animation_loop(self) -> None:
        """Advance time-correct animation at a Pi-friendly render cadence."""
        frame_started = time.perf_counter()
        elapsed = max(1 / 120, min(frame_started - self._last_frame_at, 0.1))
        self._last_frame_at = frame_started
        self.animation_engine.update_animation_frame(frame_scale=elapsed * 60.0)

        # Smoothly interpolate listening intensity (side wave fade-in / fade-out)
        target_intensity = 1.0 if self.is_listening else 0.0
        self.listening_intensity += (target_intensity - self.listening_intensity) * min(1.0, elapsed * 8.0)
        if abs(self.listening_intensity - target_intensity) < 0.01:
            self.listening_intensity = target_intensity

        render_time = time.perf_counter() - frame_started
        delay_ms = max(1, round((self._frame_interval - render_time) * 1000))
        self.after(delay_ms, self._animation_loop)

    def _draw_face(self) -> None:
        """Render ultra-fast, smooth PIL image of EMO eyes, sleep Zzz, listening waves, and charging HUD onto canvas."""
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()

        if w <= 10 or h <= 10:
            return

        now = time.time()

        # Create PIL native-resolution canvas with OLED pitch black background
        pil_img = Image.new("RGB", (w, h), (10, 10, 15))
        draw_main = ImageDraw.Draw(pil_img)

        # Calculate responsive eye base scale
        base_scale = min(w / 1104.0, h / 631.0)
        base_scale = max(0.5, base_scale)

        # Gaze and jitter offsets
        gaze_x = self.animation_engine.gaze_x + self.animation_engine.jitter_offset_x
        gaze_y = self.animation_engine.gaze_y + self.animation_engine.jitter_offset_y

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

        # Draw Floating Zzz Snore Particles if sleeping
        if self.animation_engine.is_sleeping or shape_mode == "crescent_sleep":
            self._draw_zzz_particles(draw_main, right_cx, cy, base_scale, now)

        # Draw Glowing Charging HUD Battery Indicator if charging
        if self.is_charging or self.animation_engine.is_charging:
            self._draw_charging_hud(draw_main, w, h, base_scale, now)

        # Draw Glowing Colored Listening Side Waves if active
        if self.listening_intensity > 0.01:
            self._draw_listening_waves(draw_main, w, h, base_scale, now)

        # Update Tkinter canvas image efficiently
        self._tk_img = ImageTk.PhotoImage(pil_img)
        if self._canvas_image_id is None:
            self._canvas_image_id = self.canvas.create_image(
                0, 0, image=self._tk_img, anchor="nw"
            )
        else:
            self.canvas.itemconfigure(self._canvas_image_id, image=self._tk_img)

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
        """Render individual EMO eye on isolated canvas with SSAA for ultra-crisp edges, then composite."""
        pad = int(max(width, height) * 0.8) + 40
        final_w = int(width + pad)
        final_h = int(height + pad)

        ssaa = self.supersampling
        canvas_w = final_w * ssaa
        canvas_h = final_h * ssaa

        eye_img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(eye_img)

        ecx = canvas_w / 2.0
        ecy = canvas_h / 2.0
        w_s = width * ssaa
        h_s = height * ssaa
        hw = w_s / 2.0
        hh = h_s / 2.0

        if shape_mode == "crescent_happy":
            # Happy curved crescent arc eyes (^ ^)
            self._draw_crescent_arc_eye(draw, ecx, ecy, w_s, h_s, is_left_eye, color)

        elif shape_mode == "crescent_sleep":
            # Sleeping curved crescent arc eyes (⌒ ⌒)
            self._draw_sleeping_arc_eye(draw, ecx, ecy, w_s, h_s, is_left_eye, color)

        elif shape_mode == "trapezoid_slant":
            # Slanted angry / determined / hurt trapezoid eyes (\ /)
            self._draw_trapezoid_slant_eye(
                draw, ecx, ecy, w_s, h_s, is_left_eye, slant_deg, color, core_color
            )

        elif shape_mode == "wide_oval":
            # Wide excited / surprised oval eyes (O O)
            draw.ellipse([ecx - hw, ecy - hh, ecx + hw, ecy + hh], fill=color)
            if height > width * 0.3:
                pr = min(hw, hh) * 0.42
                draw.ellipse([ecx - pr, ecy - pr, ecx + pr, ecy + pr], fill=core_color)

        else:
            # Base EMO capsule squircle
            radius = min(hw, hh) * 0.65
            draw.rounded_rectangle(
                [ecx - hw, ecy - hh, ecx + hw, ecy + hh], radius=radius, fill=color
            )

            # Circular black pupil accent
            if height > width * 0.35:
                pr = min(hw, hh) * 0.38
                draw.ellipse([ecx - pr, ecy - pr, ecx + pr, ecy + pr], fill=core_color)

        # Top eyelid clipping mask if applicable
        if eyelid_top > 0.01:
            lid_h = h_s * eyelid_top
            draw.rectangle(
                [ecx - hw - 20 * ssaa, ecy - hh - 20 * ssaa, ecx + hw + 20 * ssaa, ecy - hh + lid_h],
                fill=(0, 0, 0, 0),
            )

        # Downscale for SSAA crisp anti-aliasing
        eye_img = eye_img.resize((final_w, final_h), resample=Image.Resampling.LANCZOS)

        ecx_unscaled = final_w / 2.0
        ecy_unscaled = final_h / 2.0

        # Rotate transform for 3D perspective slant AFTER downscaling
        if abs(slant_deg) > 0.1:
            eye_img = eye_img.rotate(
                -slant_deg, resample=Image.Resampling.BICUBIC, center=(ecx_unscaled, ecy_unscaled)
            )

        # Paste rendered transformed eye onto main canvas
        px = int(cx - ecx_unscaled)
        py = int(cy - ecy_unscaled)
        pil_img.paste(eye_img, (px, py), eye_img)

    def _draw_crescent_arc_eye(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        w: float,
        h: float,
        is_left: bool,
        color: str,
    ) -> None:
        """Draw smooth, rounded horizontal crescent arc smile eye (^ ^)."""
        hw = w / 2.0
        arch_height = max(15.0, h * 0.7)
        thickness = max(14.0, h * 0.42)

        steps = 32
        points = []
        for i in range(steps + 1):
            t = i / steps
            x = cx - hw + (t * w)
            y = (cy + arch_height * 0.35) - math.sin(t * math.pi) * arch_height
            points.append((x, y))

        draw.line(points, fill=color, width=int(thickness), joint="curve")
        r = thickness / 2.0
        for px, py in points:
            draw.ellipse([px - r, py - r, px + r, py + r], fill=color)

    def _draw_sleeping_arc_eye(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        w: float,
        h: float,
        is_left: bool,
        color: str,
    ) -> None:
        """Draw smooth, cozy inverted crescent sleeping eye (⌒ ⌒)."""
        hw = w / 2.0
        arch_height = max(14.0, h * 0.65)
        thickness = max(14.0, h * 0.38)

        steps = 32
        points = []
        for i in range(steps + 1):
            t = i / steps
            x = cx - hw + (t * w)
            # Inverted curve (curves up in center, down at sides)
            y = (cy + arch_height * 0.4) - math.sin(t * math.pi) * arch_height
            points.append((x, y))

        draw.line(points, fill=color, width=int(thickness), joint="curve")
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
        core_color: str,
    ) -> None:
        r"""Draw slanted rounded trapezoid wedge eye (\ /)."""
        hw = w / 2.0
        hh = h / 2.0

        if is_left:
            p1 = (cx - hw, cy - hh * 0.75)
            p2 = (cx + hw, cy - hh * 0.15)
            p3 = (cx + hw * 0.85, cy + hh * 0.75)
            p4 = (cx - hw * 0.85, cy + hh * 0.75)
        else:
            p1 = (cx - hw, cy - hh * 0.15)
            p2 = (cx + hw, cy - hh * 0.75)
            p3 = (cx + hw * 0.85, cy + hh * 0.75)
            p4 = (cx - hw * 0.85, cy + hh * 0.75)

        draw.polygon([p1, p2, p3, p4], fill=color)

        r_corner = min(w, h) * 0.15
        for px, py in [p1, p2, p3, p4]:
            draw.ellipse([px - r_corner, py - r_corner, px + r_corner, py + r_corner], fill=color)

        if h > w * 0.4:
            pr = min(hw, hh) * 0.35
            draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=core_color)

    def _draw_zzz_particles(
        self,
        draw: ImageDraw.ImageDraw,
        right_cx: float,
        cy: float,
        base_scale: float,
        now: float,
    ) -> None:
        """Render lightweight, smooth floating Zzz sleeping snore particles."""
        for i in range(3):
            phase = ((now * 0.30) + i * 0.33) % 1.0
            # Curved gentle drift upward and to the right
            x = (right_cx + 55 * base_scale) + (phase * 75 * base_scale) + math.sin(phase * 4.0) * (12 * base_scale)
            y = (cy - 35 * base_scale) - (phase * 105 * base_scale)

            # Bell-curve alpha fade (0 -> 1 -> 0)
            alpha = math.sin(phase * math.pi)
            if alpha <= 0.05:
                continue

            c_val = int(220 * alpha)
            color = (c_val, c_val, int(255 * alpha))
            z_size = (14 + i * 6) * base_scale

            self._draw_vector_z(draw, x, y, z_size, color)

    def _draw_vector_z(
        self,
        draw: ImageDraw.ImageDraw,
        x: float,
        y: float,
        size: float,
        color: tuple,
    ) -> None:
        """Draw clean vector 'Z' snore glyph."""
        hs = size / 2.0
        th = max(2, int(size * 0.18))
        draw.line([(x - hs, y - hs), (x + hs, y - hs)], fill=color, width=th)
        draw.line([(x + hs, y - hs), (x - hs, y + hs)], fill=color, width=th)
        draw.line([(x - hs, y + hs), (x + hs, y + hs)], fill=color, width=th)

    def _draw_charging_hud(
        self,
        draw: ImageDraw.ImageDraw,
        w: int,
        h: int,
        base_scale: float,
        now: float,
    ) -> None:
        """Render sleek, animated OLED charging indicator with pulsing lightning bolt."""
        batt_w = 95 * base_scale
        batt_h = 28 * base_scale
        bx = (w * 0.5) - (batt_w * 0.5)
        by = 22 * base_scale
        rad = 7 * base_scale

        # 1. Subtle glowing dark background pill
        draw.rounded_rectangle(
            [bx, by, bx + batt_w, by + batt_h],
            radius=rad,
            fill=(18, 24, 27),
            outline=(80, 250, 123) if self.is_charging else (69, 71, 90),
            width=int(max(1, 2 * base_scale)),
        )

        # 2. Positive battery terminal cap
        cap_w = 4 * base_scale
        cap_h = batt_h * 0.45
        cap_y = by + (batt_h - cap_h) / 2.0
        draw.rounded_rectangle(
            [bx + batt_w, cap_y, bx + batt_w + cap_w, cap_y + cap_h],
            radius=2 * base_scale,
            fill=(80, 250, 123) if self.is_charging else (69, 71, 90),
        )

        # 3. Animated charging progress fill
        pct = max(0, min(100, self.battery_pct if self.battery_pct > 0 else 75))
        fill_margin = 3 * base_scale
        max_fill_w = batt_w - (fill_margin * 2)
        current_fill_w = max_fill_w * (pct / 100.0)

        if current_fill_w > 2:
            pulse = 0.85 + 0.15 * math.sin(now * 3.5)
            g_val = int(250 * pulse)
            fill_color = (0, g_val, int(170 * pulse)) if self.is_charging else (166, 227, 161)

            draw.rounded_rectangle(
                [bx + fill_margin, by + fill_margin, bx + fill_margin + current_fill_w, by + batt_h - fill_margin],
                radius=max(2.0, rad - fill_margin),
                fill=fill_color,
            )

            # Sweeping energy wave highlight
            if self.is_charging:
                wave_phase = (now * 1.3) % 1.0
                wave_x = bx + fill_margin + (wave_phase * current_fill_w)
                draw.line(
                    [(wave_x, by + fill_margin + 2), (wave_x, by + batt_h - fill_margin - 2)],
                    fill=(255, 255, 255),
                    width=int(max(1, 3 * base_scale)),
                )

        # 4. Pulsing Electric ⚡ Lightning Bolt icon
        bolt_x = bx - 20 * base_scale
        bolt_y = by + batt_h / 2.0
        bolt_size = 14 * base_scale
        bolt_color = (255, 235, 59)
        self._draw_lightning_bolt(draw, bolt_x, bolt_y, bolt_size, bolt_color, now)

    def _draw_lightning_bolt(
        self,
        draw: ImageDraw.ImageDraw,
        cx: float,
        cy: float,
        size: float,
        color: tuple,
        now: float,
    ) -> None:
        """Draw a vibrant ⚡ lightning bolt glyph."""
        s = (size / 2.0) * (1.0 + 0.12 * math.sin(now * 4.0))

        pts = [
            (cx + s * 0.1, cy - s),
            (cx - s * 0.6, cy + s * 0.1),
            (cx - s * 0.05, cy + s * 0.1),
            (cx - s * 0.25, cy + s),
            (cx + s * 0.7, cy - s * 0.15),
            (cx + s * 0.05, cy - s * 0.15),
        ]
        draw.polygon(pts, fill=color)

    def _draw_listening_waves(
        self,
        draw: ImageDraw.ImageDraw,
        w: int,
        h: int,
        base_scale: float,
        now: float,
    ) -> None:
        """
        Render dynamic, multi-harmonic glowing colored audio/frequency waves on left & right screen sides.
        Visualizes active listening state (wake word / Gemini live listening) modulated by mic audio level.
        """
        intensity = self.listening_intensity
        if intensity <= 0.01:
            return

        # Audio boost factor from real-time microphone level
        mic_boost = 1.0 + (self.current_mic_level * 5.0)

        # Harmonic layer definitions: (Color RGB, speed, freq, phase_offset, stroke_width, amp_scale)
        wave_layers = [
            ((0, 240, 255), 4.2, 3.0, 0.0, 2.5, 1.0),       # Electric Cyan
            ((255, 110, 200), -3.5, 3.8, 1.5, 2.0, 0.75),   # Magenta / Violet
        ]

        cy = h * 0.5
        wave_height = h * 0.65
        half_h = wave_height / 2.0
        steps = 18

        # Base margin from edges
        left_base_x = 36 * base_scale
        right_base_x = w - (36 * base_scale)
        max_amplitude = (18 * base_scale) * mic_boost * intensity

        # Render Left & Right Waves
        for base_x, direction in [(left_base_x, 1.0), (right_base_x, -1.0)]:
            for rgb_color, speed, freq, phase_off, stroke_w, amp_scale in wave_layers:
                col = tuple(max(0, min(255, int(c * intensity))) for c in rgb_color)
                line_width = max(1, int(stroke_w * base_scale))

                points = []
                for i in range(steps + 1):
                    t = i / steps
                    y = (cy - half_h) + (t * wave_height)
                    envelope = math.sin(t * math.pi)
                    angle = (t * math.pi * freq) + (now * speed) + phase_off
                    dx = math.sin(angle) * max_amplitude * amp_scale * envelope * direction
                    points.append((base_x + dx, y))

                if len(points) >= 2:
                    draw.line(points, fill=col, width=line_width, joint="curve")

            # Sleek center energy equalizer bars
            num_bars = 5
            bar_spacing = (wave_height * 0.40) / max(1, num_bars - 1)
            bar_start_y = cy - (wave_height * 0.20)

            for bi in range(num_bars):
                by = bar_start_y + (bi * bar_spacing)
                benv = math.sin((bi / max(1, num_bars - 1)) * math.pi)
                pulse = math.sin((now * 5.0) + bi * 1.0) * 0.5 + 0.5
                bar_len = ((6.0 + pulse * 12.0 * mic_boost) * base_scale * intensity) * benv
                bx1 = base_x - (direction * 14 * base_scale)
                bx2 = bx1 + (direction * bar_len)
                b_color = (
                    int(0 * intensity),
                    int(240 * (0.6 + 0.4 * pulse) * intensity),
                    int(255 * intensity),
                )
                draw.line([(bx1, by), (bx2, by)], fill=b_color, width=max(1, int(2 * base_scale)))

    def trigger_reaction(self, reaction_type: str) -> None:
        """Trigger an emotional eye reaction using EyeAnimationEngine."""
        self.animation_engine.trigger_reaction(reaction_type, self.after)
