"""
Low battery reaction animation handler.
Triggered when system battery drops or power is low.
"""

from .base import BaseReaction

class LowBatteryReaction(BaseReaction):
    """Low battery / Drowsy reaction (Purple glow, lowered eyelids)."""

    def __init__(self):
        super().__init__(
            name="low_battery",
            color="#CBA6F7",       # Low Battery Lavender/Purple
            core_color="#FAF5FF",
            duration_ms=2000,
            height_scale_mult=0.5  # Heavy low eyelids
        )
