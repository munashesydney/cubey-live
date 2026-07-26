"""
Hurt reaction animation handler.
Triggered when physical events like kicks or spills occur.
"""

from .base import BaseReaction

class HurtReaction(BaseReaction):
    """Hurt / Shocked physical reaction (Coral Red glow, squished eyes)."""

    def __init__(self):
        super().__init__(
            name="hurt",
            color="#F38BA8",       # Coral Red / Hurt Warning
            core_color="#FCEEED",  # Flash core
            duration_ms=1800,
            height_scale_mult=0.65 # Squished pain reaction
        )
