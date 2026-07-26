"""
Hurt reaction animation handler.
Triggered when physical events like kicks or spills occur.
"""

from .base import BaseReaction

class HurtReaction(BaseReaction):
    """Hurt / Shocked physical reaction (Coral Red eyes, black pupil, squished shiver)."""

    def __init__(self):
        super().__init__(
            name="hurt",
            color="#F38BA8",       # Coral Red
            core_color="#000000",  # Black pupil
            duration_ms=1800,
            height_scale_mult=0.60,# Squished pain reaction
            slant_angle=12.0,      # Slanted pain eyes
            jitter=True            # Pain shiver jitter
        )
