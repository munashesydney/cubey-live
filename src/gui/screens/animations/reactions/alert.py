"""
Alert reaction animation handler.
Triggered when obstacles or movement outside frame are detected.
"""

from .base import BaseReaction

class AlertReaction(BaseReaction):
    """Alert / Surprised reaction (Peach/Orange eyes, black pupil)."""

    def __init__(self):
        super().__init__(
            name="alert",
            color="#FAB387",       # Alert Peach/Orange
            core_color="#000000",  # Black pupil
            duration_ms=1600,
            height_scale_mult=1.25 # Widened surprised eyes
        )
