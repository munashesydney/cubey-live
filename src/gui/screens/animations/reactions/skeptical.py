"""
Skeptical reaction animation handler.
Triggered on curious, confused, or questioning events.
"""

from .base import BaseReaction

class SkepticalReaction(BaseReaction):
    """Skeptical / Curious reaction (One tilted raised eye, one squinted eye)."""

    def __init__(self):
        super().__init__(
            name="skeptical",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=1800,
            height_scale_mult=1.0,
            width_scale_mult=1.0,
            slant_angle=-12.0,     # Asymmetric slant
            shape_mode="trapezoid_slant",
            eyelid_top=0.25,
            squish_x=1.1,
            squish_y=0.9
        )
