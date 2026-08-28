"""
CustomTkinter GUI application module.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .windows.app_window import GeminiLiveApp

__all__ = ["GeminiLiveApp"]


def __getattr__(name: str):
    """Keep non-visual GUI helpers importable in headless processes/tests."""
    if name == "GeminiLiveApp":
        from .windows.app_window import GeminiLiveApp

        return GeminiLiveApp
    raise AttributeError(name)
