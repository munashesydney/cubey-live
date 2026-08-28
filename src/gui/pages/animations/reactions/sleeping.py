"""
Sleeping reaction animation handler.
Triggered when robot enters sleep/rest mode.
"""

from .base import BaseReaction


class SleepingReaction(BaseReaction):
    """Sleeping reaction: Peaceful closed curved sleeping eyes (⌒ ⌒)."""

    def __init__(self):
        super().__init__(
            name="sleeping",
            color="#FFFFFF",        # Soft pure white eye arcs
            core_color="#000000",   # Solid black pupil
            duration_ms=3000,
            height_scale_mult=0.50,
            width_scale_mult=1.05,
            shape_mode="crescent_sleep",
            slant_angle=0.0,
            squish_x=1.0,
            squish_y=0.9,
        )
