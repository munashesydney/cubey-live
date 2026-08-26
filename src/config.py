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
    
    # Audio parameters. 20 ms input packets are short enough for responsive VAD
    # without creating excessive WebSocket overhead. Queue limits are expressed
    # in milliseconds so a stalled device/network can never drift seconds behind
    # the live conversation.
    input_sample_rate: int = int(os.getenv("INPUT_SAMPLE_RATE", "16000"))
    output_sample_rate: int = int(os.getenv("OUTPUT_SAMPLE_RATE", "24000"))
    channels: int = int(os.getenv("CHANNELS", "1"))
    chunk_size: int = int(os.getenv("AUDIO_CHUNK_SIZE", "320"))
    input_queue_ms: int = int(os.getenv("AUDIO_INPUT_QUEUE_MS", "240"))
    output_block_size: int = int(os.getenv("AUDIO_OUTPUT_BLOCK_SIZE", "240"))
    # Diagnostic threshold only: generated speech is never frame-dropped when
    # Gemini delivers it faster than physical speakers can play it.
    output_buffer_ms: int = int(os.getenv("AUDIO_OUTPUT_BUFFER_MS", "2000"))
    input_device: str = os.getenv("AUDIO_INPUT_DEVICE", "")
    output_device: str = os.getenv("AUDIO_OUTPUT_DEVICE", "")
    device_sample_rate: int = int(os.getenv("AUDIO_DEVICE_SAMPLE_RATE", "0"))
    device_channels: int = int(os.getenv("AUDIO_DEVICE_CHANNELS", "0"))
    device_dtype: str = os.getenv("AUDIO_DEVICE_DTYPE", "")
    prefer_low_latency_devices: bool = os.getenv(
        "AUDIO_PREFER_LOW_LATENCY_DEVICE", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}

    # Gemini server VAD. Short end-of-speech detection is the largest perceived
    # latency win after audio buffering; all values remain environment-tunable.
    vad_prefix_padding_ms: int = int(os.getenv("GEMINI_VAD_PREFIX_MS", "20"))
    vad_silence_duration_ms: int = int(os.getenv("GEMINI_VAD_SILENCE_MS", "150"))

    # Retry transient Live WebSocket failures inside the same app conversation.
    live_reconnect_attempts: int = int(os.getenv("GEMINI_RECONNECT_ATTEMPTS", "5"))
    live_reconnect_base_delay: float = float(
        os.getenv("GEMINI_RECONNECT_BASE_DELAY", "0.5")
    )
    live_reconnect_max_delay: float = float(
        os.getenv("GEMINI_RECONNECT_MAX_DELAY", "5.0")
    )

    # Database Parameters
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")
    database_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)

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
