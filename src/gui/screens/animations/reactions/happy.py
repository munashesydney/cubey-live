"""
Happy reaction animation handler.
Triggered for warm, joyful, or positive interactions.
"""

from .base import BaseReaction

class HappyReaction(BaseReaction):
    """Happy / Friendly reaction (Soft Emerald Green glow)."""

    def __init__(self):
        super().__init__(
            name="happy",
            color="#A6E3A1",       # Soft Emerald Green
            core_color="#F0FDF4",
            duration_ms=1500,
            height_scale_mult=1.05
        )
