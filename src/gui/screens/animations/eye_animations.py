"""
Eye Animation Engine module.
Coordinates 60 FPS natural random blinking and loads dedicated visual reaction modules.
"""

import random
import logging
from typing import Callable, Optional

from .reactions import BaseReaction, HurtReaction, AlertReaction, HappyReaction, LowBatteryReaction

logger = logging.getLogger(__name__)

class EyeAnimationEngine:
    """Manages eye animation state, blinking cycles, and reaction transitions."""

    def __init__(self, redraw_callback: Callable[[], None]):
        self.redraw_callback = redraw_callback

        # Default styling matching Cubeo design
        self.default_color = "#89CFF0"
        self.default_core_color = "#E0F7FA"

        # Current state
        self.current_color = self.default_color
        self.current_core_color = self.default_core_color
        self.height_pct = 1.0
        self.height_scale_mult = 1.0

        self.is_blinking = False
        self.blink_step = 0
        self.emotion_name = "normal"
        self._reset_timer_id: Optional[str] = None

        # Registry of reaction animation modules
        self.reactions: dict[str, BaseReaction] = {
            "normal": BaseReaction(),
            "hurt": HurtReaction(),
            "alert": AlertReaction(),
            "happy": HappyReaction(),
            "low_battery": LowBatteryReaction(),
            "shocked": HurtReaction(),
        }

    def update_animation_frame(self) -> None:
        """Advance animation frame (called every 16ms for 60 FPS)."""
        if self.is_blinking:
            if self.blink_step <= 4:
                # Closing
                self.height_pct = max(0.05, 1.0 - (self.blink_step / 4.0) * 0.95)
            elif self.blink_step <= 8:
                # Opening
                self.height_pct = min(1.0, 0.05 + ((self.blink_step - 4) / 4.0) * 0.95)
            else:
                # Complete
                self.height_pct = 1.0
                self.is_blinking = False
            
            self.blink_step += 1

        self.redraw_callback()

    def start_blink(self) -> None:
        """Start a blink cycle if in normal emotion state."""
        if not self.is_blinking and self.emotion_name == "normal":
            self.is_blinking = True
            self.blink_step = 0

    def trigger_reaction(self, reaction_type: str, schedule_after_fn: Callable[[int, Callable], str]) -> None:
        """
        Trigger a specific visual reaction animation by type name.
        Uses schedule_after_fn (e.g. self.after) to auto-reset back to normal.
        """
        clean_type = reaction_type.lower().strip()
        reaction = self.reactions.get(clean_type, self.reactions["normal"])

        logger.info("Executing visual reaction animation: '%s'", reaction.name)
        self.emotion_name = reaction.name
        self.current_color = reaction.color
        self.current_core_color = reaction.core_color
        self.height_scale_mult = reaction.height_scale_mult

        self.redraw_callback()

        # Auto-reset back to normal after duration
        def reset_fn():
            self.emotion_name = "normal"
            self.current_color = self.default_color
            self.current_core_color = self.default_core_color
            self.height_scale_mult = 1.0
            self.redraw_callback()

        schedule_after_fn(reaction.duration_ms, reset_fn)
