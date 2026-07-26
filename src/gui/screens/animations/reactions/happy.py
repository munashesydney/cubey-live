"""
Happy reaction animation handler.
Triggered for warm, joyful, or positive interactions.
"""

from .base import BaseReaction

class HappyReaction(BaseReaction):
    """Happy / Friendly reaction (Soft Green eyes, black pupil)."""

    def __init__(self):
        super().__init__(
            name="happy",
            color="#A6E3A1",       # Soft Emerald Green
            core_color="#000000",  # Black pupil
            duration_ms=1500,
            height_scale_mult=1.05
        )
