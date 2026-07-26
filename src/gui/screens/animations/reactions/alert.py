"""
Alert reaction animation handler.
Triggered when obstacles or movement outside frame are detected.
"""

from .base import BaseReaction

class AlertReaction(BaseReaction):
    """Alert / Surprised reaction (Wide enlarged oval eyes)."""

    def __init__(self):
        super().__init__(
            name="alert",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=1600,
            height_scale_mult=1.35, # Widened surprised height
            width_scale_mult=1.25,  # Widened surprised width
            shape_mode="wide_oval",
            squish_x=1.1,
            squish_y=1.2
        )
