"""
Audio module for real-time microphone recording and speaker playback.
"""

from .recorder import AudioRecorder
from .player import AudioPlayer

__all__ = ["AudioRecorder", "AudioPlayer"]
