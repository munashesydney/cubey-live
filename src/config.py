"""
Configuration settings for the Gemini Live Voice & Interruption Simulator.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

@dataclass
class AppConfig:
    """Application configuration parameters."""
    api_key: str = os.getenv("GEMINI_API_KEY", "")
    model: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
    voice_name: str = os.getenv("GEMINI_VOICE", "Puck")
    
    # Audio Parameters (Optimized for low latency across Windows and Linux)
    input_sample_rate: int = int(os.getenv("INPUT_SAMPLE_RATE", "16000"))
    output_sample_rate: int = int(os.getenv("OUTPUT_SAMPLE_RATE", "24000"))
    channels: int = int(os.getenv("CHANNELS", "1"))
    chunk_size: int = 512  # low-latency frame buffer size

    def validate(self) -> None:
        """Validate critical configuration fields."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please set GEMINI_API_KEY in your environment or in a .env file."
            )

config = AppConfig()
