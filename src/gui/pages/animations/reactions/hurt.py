"""
Hurt reaction animation handler.
Triggered when physical events like kicks or spills occur.
"""

from .base import BaseReaction

class HurtReaction(BaseReaction):
    """Hurt / Pain physical reaction (Slanted inward pain eyes, jitter shiver)."""

    def __init__(self):
        super().__init__(
            name="hurt",
            color="#FFFFFF",       # Pure white eyes
            core_color="#000000",  # Black pupil
            duration_ms=1800,
            height_scale_mult=0.75,
            width_scale_mult=1.1,
            shape_mode="trapezoid_slant",
            slant_angle=18.0,      # Slanted pain angle
            jitter=True,            # Pain shiver jitter
            squish_x=1.15,
            squish_y=0.7
        )
