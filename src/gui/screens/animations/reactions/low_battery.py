"""
Low battery reaction animation handler.
Triggered when system battery drops or power is low.
"""

from .base import BaseReaction

class LowBatteryReaction(BaseReaction):
    """Low battery / Drowsy reaction (Purple eyes, black pupil)."""

    def __init__(self):
        super().__init__(
            name="low_battery",
            color="#CBA6F7",       # Purple
            core_color="#000000",  # Black pupil
            duration_ms=2000,
            height_scale_mult=0.5  # Heavy low eyelids
        )
