"""
Alert reaction animation handler.
Triggered when obstacles or movement outside frame are detected.
"""

from .base import BaseReaction

class AlertReaction(BaseReaction):
    """Alert / Surprised reaction (Peach/Orange glow, widened eyes)."""

    def __init__(self):
        super().__init__(
            name="alert",
            color="#FAB387",       # Alert Peach/Orange
            core_color="#FFF5EE",
            duration_ms=1600,
            height_scale_mult=1.2  # Widened surprised eyes
        )
