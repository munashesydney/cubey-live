"""
Charging reaction animation handler.
Triggered when robot is connected to power or charging.
"""

from .base import BaseReaction


class ChargingReaction(BaseReaction):
    """Charging reaction: Energized electric emerald/cyan eyes with happy arc pulses."""

    def __init__(self):
        super().__init__(
            name="charging",
            color="#50FA7B",        # Vibrant electric charging green
            core_color="#000000",   # Solid black pupil
            duration_ms=2500,
            height_scale_mult=0.55,
            width_scale_mult=1.05,
            shape_mode="crescent_happy",
            slant_angle=0.0,
            squish_x=1.05,
            squish_y=0.95,
        )
