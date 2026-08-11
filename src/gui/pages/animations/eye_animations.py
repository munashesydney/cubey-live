"""
Eye Animation Engine module.
Coordinates 60 FPS organic random micro-gaze saccades, EMO-style spring physics,
3D perspective tilt/shear, shape morphing, variable blink/wink patterns,
and dynamic EMO idle action sequences.
"""

import math
import random
import time
import logging
from typing import Callable, Optional, Dict

from .reactions import (
    BaseReaction,
    HurtReaction,
    AlertReaction,
    HappyReaction,
    LowBatteryReaction,
    SurprisedReaction,
    SkepticalReaction,
)

logger = logging.getLogger(__name__)

def ease_out_cubic(t: float) -> float:
    """Cubic ease-out interpolation for natural organic motion deceleration."""
    t = max(0.0, min(1.0, t))
    return 1.0 - math.pow(1.0 - t, 3)

def ease_out_bounce(t: float) -> float:
    """Bounce ease-out physics for elastic landings."""
    t = max(0.0, min(1.0, t))
    n1 = 7.5625
    d1 = 2.75

    if t < 1 / d1:
        return n1 * t * t
    elif t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    elif t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    else:
        t -= 2.625 / d1
        return n1 * t * t + 0.984375

def smoothstep(t: float) -> float:
    """Smoothstep S-curve interpolation."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)

class EyeAnimationEngine:
    """Manages full EMO-style eye animation state, multi-pattern blinks, spring physics, and idle behavior state machine."""

    def __init__(self, redraw_callback: Callable[[], None]):
        self.redraw_callback = redraw_callback

        # Visual theme colors: Crisp pure white eyes with dark pupil
        self.default_color = "#FFFFFF"
        self.default_core_color = "#000000"

        self.current_color = self.default_color
        self.current_core_color = self.default_core_color

        # Motion & Gaze parameters
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.target_gaze_x = 0.0
        self.target_gaze_y = 0.0
        self.start_gaze_x = 0.0
        self.start_gaze_y = 0.0
        self.saccade_t = 1.0

        # Jitter offsets (pain / shock)
        self.jitter_offset_x = 0.0
        self.jitter_offset_y = 0.0

        # Per-eye transform & shape parameters (Left & Right)
        self.slant_left = 0.0      # Left eye slant (degrees)
        self.slant_right = 0.0     # Right eye slant (degrees)
        self.target_slant_l = 0.0
        self.target_slant_r = 0.0

        self.shear_x = 0.0          # 3D perspective horizontal shear
        self.target_shear_x = 0.0

        self.squish_l_x = 1.0      # Horizontal scale/squish (left)
        self.squish_l_y = 1.0      # Vertical scale/squish (left)
        self.squish_r_x = 1.0      # Horizontal scale/squish (right)
        self.squish_r_y = 1.0      # Vertical scale/squish (right)

        self.target_squish_l_x = 1.0
        self.target_squish_l_y = 1.0
        self.target_squish_r_x = 1.0
        self.target_squish_r_y = 1.0

        self.eyelid_top_left = 0.0   # Top eyelid closure (0.0 open, 1.0 closed)
        self.eyelid_top_right = 0.0
        self.eyelid_bottom_left = 0.0
        self.eyelid_bottom_right = 0.0

        self.target_eyelid_top_l = 0.0
        self.target_eyelid_top_r = 0.0

        self.shape_mode = "capsule"  # "capsule", "crescent_happy", "trapezoid_slant", "wide_oval"
        self.pending_shape_mode = None

        self.height_scale_mult = 1.0
        self.width_scale_mult = 1.0

        # Blinking state machine
        self.is_blinking = False
        self.blink_mode = "NORMAL"   # "NORMAL", "DOUBLE", "SLEEPY", "WINK_LEFT", "WINK_RIGHT", "FLUTTER"
        self.blink_step = 0
        self.max_blink_steps = 10
        self.blink_scale_l = 1.0
        self.blink_scale_r = 1.0

        # Idle Action State Machine
        self.is_running_action = False
        self.action_name = ""
        self.action_start_time = 0.0
        self.action_duration = 0.0

        self.emotion_name = "normal"
        self._current_reaction: BaseReaction = BaseReaction()

        # Registry of reactions
        self.reactions: Dict[str, BaseReaction] = {
            "normal": BaseReaction(),
            "hurt": HurtReaction(),
            "alert": AlertReaction(),
            "happy": HappyReaction(),
            "low_battery": LowBatteryReaction(),
            "surprised": SurprisedReaction(),
            "skeptical": SkepticalReaction(),
            "shocked": HurtReaction(),
        }

        # Timers
        self._last_saccade_time = time.time()
        self._next_saccade_interval = random.uniform(1.8, 3.8)
        self._last_action_time = time.time()
        self._next_action_interval = random.uniform(3.5, 6.5)

    @property
    def effective_height_scale(self) -> float:
        """Backwards compatibility accessor for height scale multiplier."""
        return self.height_scale_mult

    @property
    def gaze_offset_x(self) -> float:
        return self.gaze_x

    @property
    def gaze_offset_y(self) -> float:
        return self.gaze_y

    @property
    def slant_angle(self) -> float:
        return (self.slant_left + self.slant_right) / 2.0

    def update_animation_frame(self) -> None:
        """Advance animation state by 1 frame (~16ms at 60 FPS)."""
        now = time.time()

        # 1. Active Custom EMO Action Sequence Update
        if self.is_running_action:
            self._update_current_action(now)
        else:
            # Periodic EMO Action Trigger (only during 'normal' emotion state)
            if self.emotion_name == "normal" and (now - self._last_action_time >= self._next_action_interval):
                if random.random() < 0.65:
                    self._trigger_random_emo_action()
                    self._last_action_time = now
                    self._next_action_interval = random.uniform(4.0, 7.5)

            # 2. Organic Micro-Gaze Saccades (when not running custom action)
            if not self.is_running_action and (now - self._last_saccade_time >= self._next_saccade_interval):
                self._trigger_random_saccade()
                self._last_saccade_time = now
                self._next_saccade_interval = random.uniform(1.8, 3.8)

        # Smooth Gaze Interpolation
        if self.saccade_t < 1.0:
            self.saccade_t += 0.075
            progress = ease_out_cubic(self.saccade_t)
            self.gaze_x = self.start_gaze_x + (self.target_gaze_x - self.start_gaze_x) * progress
            self.gaze_y = self.start_gaze_y + (self.target_gaze_y - self.start_gaze_y) * progress
        else:
            self.gaze_x = self.target_gaze_x
            self.gaze_y = self.target_gaze_y

        # 3. Update Blinking Cycle
        self._update_blink_cycle()

        # Check for pending shape change masked by blink
        if self.pending_shape_mode and self.pending_shape_mode != self.shape_mode:
            if not self.is_blinking:
                self.start_blink("TRANSITION")
            elif self.blink_step >= self.max_blink_steps / 2.0:
                self.shape_mode = self.pending_shape_mode
                self.pending_shape_mode = None

        # 4. Spring Physics Lerp for Slants, Shear, Squish, and Eyelids
        lerp_speed = 0.18
        self.slant_left += (self.target_slant_l - self.slant_left) * lerp_speed
        self.slant_right += (self.target_slant_r - self.slant_right) * lerp_speed
        self.shear_x += (self.target_shear_x - self.shear_x) * lerp_speed

        self.squish_l_x += (self.target_squish_l_x - self.squish_l_x) * lerp_speed
        self.squish_l_y += (self.target_squish_l_y - self.squish_l_y) * lerp_speed
        self.squish_r_x += (self.target_squish_r_x - self.squish_r_x) * lerp_speed
        self.squish_r_y += (self.target_squish_r_y - self.squish_r_y) * lerp_speed

        self.eyelid_top_left += (self.target_eyelid_top_l - self.eyelid_top_left) * lerp_speed
        self.eyelid_top_right += (self.target_eyelid_top_r - self.eyelid_top_right) * lerp_speed

        # 5. Idle Breathing Wave (Subtle natural vertical scale pulse)
        if not self.is_running_action:
            breathing = math.sin(now * 2.4) * 0.02
            self.squish_l_y_effective = self.squish_l_y + breathing
            self.squish_r_y_effective = self.squish_r_y + breathing
        else:
            self.squish_l_y_effective = self.squish_l_y
            self.squish_r_y_effective = self.squish_r_y

        # 6. Pain / Jitter Shiver
        if self._current_reaction and self._current_reaction.jitter:
            self.jitter_offset_x = random.uniform(-5.0, 5.0)
            self.jitter_offset_y = random.uniform(-5.0, 5.0)
        else:
            self.jitter_offset_x = 0.0
            self.jitter_offset_y = 0.0

        self.redraw_callback()

    def start_blink(self, mode: Optional[str] = None) -> None:
        """Start a blink cycle with optional mode specification."""
        if not self.is_blinking:
            self.is_blinking = True
            self.blink_step = 0
            if mode:
                self.blink_mode = mode
                if mode == "TRANSITION":
                    self.max_blink_steps = 12
            else:
                # Random blink pattern selection
                r = random.random()
                if r < 0.65:
                    self.blink_mode = "NORMAL"
                    self.max_blink_steps = 10
                elif r < 0.80:
                    self.blink_mode = "DOUBLE"
                    self.max_blink_steps = 18
                elif r < 0.90:
                    self.blink_mode = "WINK_LEFT" if random.random() < 0.5 else "WINK_RIGHT"
                    self.max_blink_steps = 12
                else:
                    self.blink_mode = "SLEEPY"
                    self.max_blink_steps = 22

    def _update_blink_cycle(self) -> None:
        """Process active blink or wink frame."""
        if not self.is_blinking:
            self.blink_scale_l = 1.0
            self.blink_scale_r = 1.0
            return

        total = float(self.max_blink_steps)
        half = total / 2.0

        if self.blink_mode == "NORMAL":
            if self.blink_step <= half:
                t = self.blink_step / half
                val = max(0.04, 1.0 - smoothstep(t) * 0.96)
            else:
                t = (self.blink_step - half) / half
                val = min(1.0, 0.04 + smoothstep(t) * 0.96)
            self.blink_scale_l = val
            self.blink_scale_r = val

        elif self.blink_mode == "DOUBLE":
            # Double blink wave (two fast peaks)
            cycle = (self.blink_step / total) * (2 * math.pi)
            val = max(0.04, (math.cos(cycle) + 1.0) / 2.0)
            self.blink_scale_l = val
            self.blink_scale_r = val

        elif self.blink_mode == "WINK_LEFT":
            if self.blink_step <= half:
                val = max(0.04, 1.0 - smoothstep(self.blink_step / half) * 0.96)
            else:
                val = min(1.0, 0.04 + smoothstep((self.blink_step - half) / half) * 0.96)
            self.blink_scale_l = val
            self.blink_scale_r = 1.0

        elif self.blink_mode == "WINK_RIGHT":
            if self.blink_step <= half:
                val = max(0.04, 1.0 - smoothstep(self.blink_step / half) * 0.96)
            else:
                val = min(1.0, 0.04 + smoothstep((self.blink_step - half) / half) * 0.96)
            self.blink_scale_l = 1.0
            self.blink_scale_r = val

        elif self.blink_mode == "SLEEPY":
            # Slow sleepy blink
            if self.blink_step <= half:
                val = max(0.1, 1.0 - (self.blink_step / half) * 0.9)
            else:
                val = min(1.0, 0.1 + ((self.blink_step - half) / half) * 0.9)
            self.blink_scale_l = val
            self.blink_scale_r = val

        elif self.blink_mode == "TRANSITION":
            if self.blink_step <= half:
                val = max(0.01, 1.0 - smoothstep(self.blink_step / half) * 0.99)
            else:
                val = min(1.0, 0.01 + smoothstep((self.blink_step - half) / half) * 0.99)
            self.blink_scale_l = val
            self.blink_scale_r = val

        self.blink_step += 1
        if self.blink_step > self.max_blink_steps:
            self.is_blinking = False
            self.blink_scale_l = 1.0
            self.blink_scale_r = 1.0

    def _trigger_random_saccade(self) -> None:
        """Trigger random eye glance target with organic range."""
        if self.emotion_name == "normal":
            if random.random() < 0.4:
                new_x, new_y = 0.0, 0.0
            else:
                new_x = random.uniform(-45.0, 45.0)
                new_y = random.uniform(-25.0, 25.0)

            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = new_x
            self.target_gaze_y = new_y
            self.saccade_t = 0.0

    def _trigger_random_emo_action(self) -> None:
        """Pick and launch a fun EMO robot idle action."""
        actions = [
            "3D_TWIST",
            "CORNER_SQUISH",
            "BOUNCE_STRETCH",
            "EYE_ROLL",
            "ARC_SMILE_PULSE",
            "DOUBLE_TAKE",
            "CURIOSITY_TILT",
        ]
        chosen = random.choice(actions)
        self.is_running_action = True
        self.action_name = chosen
        self.action_start_time = time.time()

        if chosen == "3D_TWIST":
            self.action_duration = 1.6
            tilt = random.choice([-20.0, 20.0])
            self.target_slant_l = tilt
            self.target_slant_r = -tilt
            self.target_shear_x = 0.25 if tilt > 0 else -0.25
            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = 40.0 if tilt > 0 else -40.0
            self.target_gaze_y = -15.0
            self.saccade_t = 0.0

        elif chosen == "CORNER_SQUISH":
            self.action_duration = 1.4
            side_x = random.choice([-65.0, 65.0])
            side_y = random.choice([-35.0, 35.0])
            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = side_x
            self.target_gaze_y = side_y
            self.saccade_t = 0.0
            # Squish against corner edge
            self.target_squish_l_x = 0.75
            self.target_squish_l_y = 1.25
            self.target_squish_r_x = 0.75
            self.target_squish_r_y = 1.25

        elif chosen == "BOUNCE_STRETCH":
            self.action_duration = 1.2
            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = 0.0
            self.target_gaze_y = -40.0  # Jump up
            self.saccade_t = 0.0
            self.target_squish_l_y = 1.4
            self.target_squish_l_x = 0.8
            self.target_squish_r_y = 1.4
            self.target_squish_r_x = 0.8

        elif chosen == "EYE_ROLL":
            self.action_duration = 2.0

        elif chosen == "ARC_SMILE_PULSE":
            self.action_duration = 1.8
            self.pending_shape_mode = "crescent_happy"
            self.target_squish_l_y = 0.55
            self.target_squish_r_y = 0.55

        elif chosen == "DOUBLE_TAKE":
            self.action_duration = 1.8
            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = -50.0
            self.target_gaze_y = 0.0
            self.saccade_t = 0.0
            self.start_blink("DOUBLE")

        elif chosen == "CURIOSITY_TILT":
            self.action_duration = 1.5
            self.target_slant_l = 16.0
            self.target_slant_r = 16.0
            self.target_eyelid_top_l = 0.2
            self.target_eyelid_top_r = 0.0

    def _update_current_action(self, now: float) -> None:
        """Process current EMO action progress."""
        elapsed = now - self.action_start_time

        if self.action_name == "EYE_ROLL":
            progress = elapsed / self.action_duration
            angle = progress * 2.0 * math.pi
            radius_x = 45.0
            radius_y = 25.0
            self.gaze_x = math.cos(angle) * radius_x
            self.gaze_y = math.sin(angle) * radius_y

        elif self.action_name == "BOUNCE_STRETCH" and elapsed > 0.5:
            # Landing squish
            self.target_gaze_y = 0.0
            self.target_squish_l_y = 0.75
            self.target_squish_l_x = 1.3
            self.target_squish_r_y = 0.75
            self.target_squish_r_x = 1.3

        if elapsed >= self.action_duration:
            # Reset action parameters back to normal
            self.is_running_action = False
            self.action_name = ""
            self.target_slant_l = 0.0
            self.target_slant_r = 0.0
            self.target_shear_x = 0.0
            self.target_squish_l_x = 1.0
            self.target_squish_l_y = 1.0
            self.target_squish_r_x = 1.0
            self.target_squish_r_y = 1.0
            self.target_eyelid_top_l = 0.0
            self.target_eyelid_top_r = 0.0
            if self.shape_mode != "capsule":
                self.pending_shape_mode = "capsule"
            
            # Fix gaze snap back by starting interpolation from CURRENT gaze
            self.start_gaze_x = self.gaze_x
            self.start_gaze_y = self.gaze_y
            self.target_gaze_x = 0.0
            self.target_gaze_y = 0.0
            self.saccade_t = 0.0

    def trigger_reaction(self, reaction_type: str, schedule_after_fn: Callable[[int, Callable], str]) -> None:
        """Trigger a specific visual reaction animation by type name."""
        clean_type = reaction_type.lower().strip()
        reaction = self.reactions.get(clean_type, self.reactions["normal"])

        logger.info("Executing EMO visual reaction animation: '%s'", reaction.name)
        self._current_reaction = reaction
        self.emotion_name = reaction.name
        self.current_color = reaction.color
        self.current_core_color = reaction.core_color
        self.height_scale_mult = reaction.height_scale_mult
        self.width_scale_mult = reaction.width_scale_mult

        self.pending_shape_mode = reaction.shape_mode

        self.target_slant_l = reaction.slant_angle
        self.target_slant_r = -reaction.slant_angle if reaction.shape_mode == "trapezoid_slant" else reaction.slant_angle

        self.target_squish_l_x = reaction.squish_x
        self.target_squish_l_y = reaction.squish_y
        self.target_squish_r_x = reaction.squish_x
        self.target_squish_r_y = reaction.squish_y

        self.target_eyelid_top_l = reaction.eyelid_top
        self.target_eyelid_top_r = reaction.eyelid_top

        self.redraw_callback()

        # Auto-reset back to normal after duration
        def reset_fn():
            self._current_reaction = self.reactions["normal"]
            self.emotion_name = "normal"
            self.current_color = self.default_color
            self.current_core_color = self.default_core_color
            self.height_scale_mult = 1.0
            self.width_scale_mult = 1.0
            self.target_slant_l = 0.0
            self.target_slant_r = 0.0
            self.target_shear_x = 0.0
            self.target_squish_l_x = 1.0
            self.target_squish_l_y = 1.0
            self.target_squish_r_x = 1.0
            self.target_squish_r_y = 1.0
            self.target_eyelid_top_l = 0.0
            self.target_eyelid_top_r = 0.0
            if self.shape_mode != "capsule":
                self.pending_shape_mode = "capsule"
            self.jitter_offset_x = 0.0
            self.jitter_offset_y = 0.0
            self.redraw_callback()

        schedule_after_fn(reaction.duration_ms, reset_fn)
