"""
Configuration settings for Cubey - the Gemini Live Voice & Interruption Simulator.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

from src.ai.prompts.local_llm import SYSTEM_PROMPT as _LOCAL_LLM_SYSTEM_PROMPT

# Load environment variables from .env if present
load_dotenv()

# Project root (two levels up from src/config.py -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default location for the SQLite database file
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "cubey.db"

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

    # Database Parameters
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")
    database_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)

    # Local speech-to-text (faster-whisper) for conversation history.
    # Runs on-device, so it is free and works offline; only the transcript
    # quality/speed trade-off changes with model size.
    stt_model_size: str = os.getenv("STT_MODEL_SIZE", "small")
    stt_language: str = os.getenv("STT_LANGUAGE", "en")

    # Local embeddings (fastembed) for semantic memory over past messages.
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

    # Native Local LLM Parameters (llama-cpp-python)
    local_model_repo_id: str = os.getenv("LOCAL_MODEL_REPO_ID", "bartowski/Qwen_Qwen3.5-2B-GGUF")
    local_model_filename: str = os.getenv("LOCAL_MODEL_FILENAME", "Qwen_Qwen3.5-2B-Q4_K_M.gguf")
    local_model_n_ctx: int = int(os.getenv("LOCAL_MODEL_N_CTX", "4096"))
    local_model_system_prompt: str = os.getenv(
        "LOCAL_MODEL_SYSTEM_PROMPT", _LOCAL_LLM_SYSTEM_PROMPT
    )

    def validate(self) -> None:
        """Validate critical configuration fields."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please set GEMINI_API_KEY in your environment or in a .env file."
            )

config = AppConfig()
