"""
Low battery reaction animation handler.
Triggered when system battery drops or power is low.
"""

from .base import BaseReaction

class LowBatteryReaction(BaseReaction):
    """Low battery / Drowsy reaction (Half-closed heavy eyelids)."""

    def __init__(self):
        super().__init__(
            name="low_battery",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=2200,
            height_scale_mult=0.65,
            width_scale_mult=0.95,
            eyelid_top=0.55,       # Heavy drooping eyelids
            squish_x=1.1,
            squish_y=0.7
        )
