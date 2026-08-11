"""
Surprised reaction animation handler.
Triggered on unexpected physical events or sudden interactions.
"""

from .base import BaseReaction

class SurprisedReaction(BaseReaction):
    """Surprised reaction (Wide O-shaped eyes with scale pulse)."""

    def __init__(self):
        super().__init__(
            name="surprised",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=1600,
            height_scale_mult=1.45,
            width_scale_mult=1.3,
            shape_mode="wide_oval",
            squish_x=0.9,
            squish_y=1.3
        )
