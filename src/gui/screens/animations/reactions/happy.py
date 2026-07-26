"""
Happy reaction animation handler.
Triggered for warm, joyful, or positive interactions.
"""

from .base import BaseReaction

class HappyReaction(BaseReaction):
    """Happy / Joyful reaction (Crescent Arc eyes ^ ^)."""

    def __init__(self):
        super().__init__(
            name="happy",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=1800,
            height_scale_mult=0.55, # Horizontal crescent proportions
            width_scale_mult=1.1,
            shape_mode="crescent_happy",
            slant_angle=0.0
        )
