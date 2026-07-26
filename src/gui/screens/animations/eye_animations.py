"""
Eye Animation Engine module.
Coordinates 60 FPS organic random micro-gaze saccades, cosine easing blinks,
breathing idle pulses, and reaction animation state transitions.
"""

import math
import random
import time
import logging
from typing import Callable, Optional

from .reactions import BaseReaction, HurtReaction, AlertReaction, HappyReaction, LowBatteryReaction

logger = logging.getLogger(__name__)

def ease_out_cubic(t: float) -> float:
    """Cubic ease-out interpolation for smooth natural deceleration."""
    t = max(0.0, min(1.0, t))
    return 1.0 - math.pow(1.0 - t, 3)

def smoothstep(t: float) -> float:
    """Smoothstep S-curve interpolation."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)

class EyeAnimationEngine:
    """Manages eye animation state, blinking cycles, organic saccades, and reaction transitions."""

    def __init__(self, redraw_callback: Callable[[], None]):
        self.redraw_callback = redraw_callback

        # Default visual parameters: Pure white eyes with black pupil
        self.default_color = "#FFFFFF"
        self.default_core_color = "#000000"

        # Current visual state
        self.current_color = self.default_color
        self.current_core_color = self.default_core_color
        self.height_pct = 1.0
        self.height_scale_mult = 1.0
        self.slant_angle = 0.0
        self.jitter_offset_x = 0.0
        self.jitter_offset_y = 0.0

        # Organic Micro-Gaze Saccades (Random Eye Glances)
        self.gaze_offset_x = 0.0
        self.gaze_offset_y = 0.0
        self.start_gaze_x = 0.0
        self.start_gaze_y = 0.0
        self.target_gaze_x = 0.0
        self.target_gaze_y = 0.0
        self.saccade_t = 1.0

        # Blinking state
        self.is_blinking = False
        self.blink_step = 0
        self.max_blink_steps = 10
        self.emotion_name = "normal"
        self._current_reaction: BaseReaction = BaseReaction()

        # Registry of reaction animation modules
        self.reactions: dict[str, BaseReaction] = {
            "normal": BaseReaction(),
            "hurt": HurtReaction(),
            "alert": AlertReaction(),
            "happy": HappyReaction(),
            "low_battery": LowBatteryReaction(),
            "shocked": HurtReaction(),
        }

        # Start random saccade timer
        self._last_saccade_time = time.time()
        self._next_saccade_interval = random.uniform(1.8, 4.2)

    def update_animation_frame(self) -> None:
        """Advance animation frame (called every ~16ms for 60 FPS)."""
        now = time.time()

        # 1. Update Organic Micro-Gaze Saccades
        if now - self._last_saccade_time >= self._next_saccade_interval:
            self._trigger_random_saccade()
            self._last_saccade_time = now
            self._next_saccade_interval = random.uniform(1.8, 4.2)

        if self.saccade_t < 1.0:
            self.saccade_t += 0.08
            progress = ease_out_cubic(self.saccade_t)
            self.gaze_offset_x = self.start_gaze_x + (self.target_gaze_x - self.start_gaze_x) * progress
            self.gaze_offset_y = self.start_gaze_y + (self.target_gaze_y - self.start_gaze_y) * progress
        else:
            self.gaze_offset_x = self.target_gaze_x
            self.gaze_offset_y = self.target_gaze_y

        # 2. Update Blinking Cycle with Cosine Easing
        if self.is_blinking:
            half = self.max_blink_steps / 2.0
            if self.blink_step <= half:
                # Closing (0 to half)
                t = self.blink_step / half
                self.height_pct = max(0.04, 1.0 - smoothstep(t) * 0.96)
            elif self.blink_step <= self.max_blink_steps:
                # Opening (half to max)
                t = (self.blink_step - half) / half
                self.height_pct = min(1.0, 0.04 + smoothstep(t) * 0.96)
            else:
                self.height_pct = 1.0
                self.is_blinking = False

            self.blink_step += 1

        # 3. Idle Breathing Sinusoidal Scale Wave
        breathing_pulse = math.sin(now * 2.2) * 0.015
        self.effective_height_scale = (self.height_pct + breathing_pulse) * self.height_scale_mult

        # 4. Pain Jitter / Shiver Offset
        if self._current_reaction and self._current_reaction.jitter:
            self.jitter_offset_x = random.uniform(-4.0, 4.0)
            self.jitter_offset_y = random.uniform(-4.0, 4.0)
        else:
            self.jitter_offset_x = 0.0
            self.jitter_offset_y = 0.0

        self.redraw_callback()

    def start_blink(self) -> None:
        """Start a blink cycle if in normal emotion state."""
        if not self.is_blinking and self.emotion_name == "normal":
            self.is_blinking = True
            self.blink_step = 0

    def _trigger_random_saccade(self) -> None:
        """Pick a new random eye glance target (x: -16 to +16px, y: -9 to +9px)."""
        if self.emotion_name == "normal":
            if random.random() < 0.6:
                new_x, new_y = 0.0, 0.0
            else:
                new_x = random.uniform(-16.0, 16.0)
                new_y = random.uniform(-9.0, 9.0)

            self.start_gaze_x = self.gaze_offset_x
            self.start_gaze_y = self.gaze_offset_y
            self.target_gaze_x = new_x
            self.target_gaze_y = new_y
            self.saccade_t = 0.0

    def trigger_reaction(self, reaction_type: str, schedule_after_fn: Callable[[int, Callable], str]) -> None:
        """Trigger a specific visual reaction animation by type name."""
        clean_type = reaction_type.lower().strip()
        reaction = self.reactions.get(clean_type, self.reactions["normal"])

        logger.info("Executing visual reaction animation: '%s'", reaction.name)
        self._current_reaction = reaction
        self.emotion_name = reaction.name
        self.current_color = reaction.color
        self.current_core_color = reaction.core_color
        self.height_scale_mult = reaction.height_scale_mult
        self.slant_angle = reaction.slant_angle

        self.redraw_callback()

        # Auto-reset back to normal after duration
        def reset_fn():
            self._current_reaction = self.reactions["normal"]
            self.emotion_name = "normal"
            self.current_color = self.default_color
            self.current_core_color = self.default_core_color
            self.height_scale_mult = 1.0
            self.slant_angle = 0.0
            self.jitter_offset_x = 0.0
            self.jitter_offset_y = 0.0
            self.redraw_callback()

        schedule_after_fn(reaction.duration_ms, reset_fn)
