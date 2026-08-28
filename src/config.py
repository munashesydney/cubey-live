"""
Configuration settings for Cubey - the Gemini Live Voice & Interruption Simulator.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.ai.prompts.local_llm import SYSTEM_PROMPT as _LOCAL_LLM_SYSTEM_PROMPT

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
    enable_noise_suppression: bool = os.getenv(
        "AUDIO_ENABLE_NOISE_SUPPRESSION", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    gui_fullscreen: bool = os.getenv(
        "GUI_FULLSCREEN", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    # Raspberry Pi-friendly face rendering. Animation physics remain time
    # compensated, so reducing FPS lowers CPU without slowing reactions.
    gui_face_fps: int = int(os.getenv("GUI_FACE_FPS", "30"))
    gui_face_supersampling: int = int(os.getenv("GUI_FACE_SUPERSAMPLING", "2"))

    # Full-duplex acoustic echo cancellation on Raspberry Pi. The setup
    # script creates these PipeWire/WebRTC virtual endpoints. Cubey connects
    # through the PulseAudio compatibility device so PULSE_SOURCE/PULSE_SINK
    # can route only this process without changing the desktop defaults.
    enable_echo_cancellation: bool = os.getenv(
        "AUDIO_ENABLE_ECHO_CANCELLATION", "false"
    ).strip().lower() not in {"0", "false", "no", "off"}
    echo_cancel_source: str = os.getenv(
        "AUDIO_ECHO_CANCEL_SOURCE", "cubey_echo_cancel_source"
    )
    echo_cancel_sink: str = os.getenv(
        "AUDIO_ECHO_CANCEL_SINK", "cubey_echo_cancel_sink"
    )
    echo_cancel_host_device: str = os.getenv(
        "AUDIO_ECHO_CANCEL_HOST_DEVICE", "pulse"
    )

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

    # Inactivity watchdog: Auto-close Live session if no user speech for X seconds.
    live_inactivity_timeout_seconds: float = float(
        os.getenv("LIVE_INACTIVITY_TIMEOUT_SECONDS", "10.0")
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

    # Wake Word (sherpa-onnx keyword spotter) Parameters
    enable_wake_word: bool = os.getenv(
        "WAKE_WORD_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    wake_words: str = os.getenv(
        "WAKE_WORDS",
        "HEY CUBEY, OK CUBEY, HI CUBEY, CUBEY, "
        "YO CUBEY, SUP CUBEY, WHATS UP CUBEY, WHAT'S UP CUBEY, AYO CUBEY, WHAT'S GOOD CUBEY, WHATS GOOD CUBEY, "
        "HELLO CUBEY, RISE AND SHINE, "
        "HEY Q BEE, Q BEE, YO Q BEE, SUP Q BEE, HI Q BEE, AYO Q BEE, "
        "HEY CUBE Y, CUBE Y, YO CUBE Y, "
        "HEY CUBE EE, CUBE EE, YO CUBE EE, SUP CUBE EE",
    )
    wake_word_model_dir: Path = field(
        default_factory=lambda: DEFAULT_DATA_DIR / "models" / "sherpa-onnx-kws"
    )
    wake_word_threshold: float = float(os.getenv("WAKE_WORD_THRESHOLD", "0.15"))
    wake_word_score: float = float(os.getenv("WAKE_WORD_SCORE", "2.0"))
    wake_word_gain: float = float(os.getenv("WAKE_WORD_GAIN", "2.0"))
    wake_word_threads: int = int(os.getenv("WAKE_WORD_THREADS", "2"))

    # Real-Time Camera & Multimodal Vision Parameters
    camera_device_index: int = int(os.getenv("CAMERA_DEVICE_INDEX", "0"))
    camera_fps: int = int(os.getenv("CAMERA_FPS", "15"))
    camera_preview_fps: int = int(os.getenv("CAMERA_PREVIEW_FPS", "10"))
    camera_live_fps: float = float(os.getenv("CAMERA_LIVE_FPS", "1.0"))
    camera_width: int = int(os.getenv("CAMERA_WIDTH", "640"))
    camera_height: int = int(os.getenv("CAMERA_HEIGHT", "480"))
    camera_jpeg_quality: int = int(os.getenv("CAMERA_JPEG_QUALITY", "80"))
    camera_auto_start_with_live: bool = os.getenv(
        "CAMERA_AUTO_START_WITH_LIVE", "false"
    ).strip().lower() not in {"0", "false", "no", "off"}

    # Waveshare / Slamtec RPLIDAR C1 Parameters
    lidar_port: str = os.getenv("LIDAR_PORT", "")
    lidar_baudrate: int = int(os.getenv("LIDAR_BAUDRATE", "460800"))
    lidar_safety_distance_mm: int = int(os.getenv("LIDAR_SAFETY_DISTANCE_MM", "300"))
    lidar_auto_connect: bool = os.getenv(
        "LIDAR_AUTO_CONNECT", "false"
    ).strip().lower() not in {"0", "false", "no", "off"}

    # Web Server & Remote Map Control (SLAM & Live View)
    web_server_enabled: bool = os.getenv(
        "WEB_SERVER_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    web_host: str = os.getenv("WEB_HOST", "0.0.0.0")
    web_port: int = int(os.getenv("WEB_PORT", "8000"))
    web_username: str = os.getenv("WEB_USERNAME", "admin")
    web_password: str = os.getenv("WEB_PASSWORD", "cubey")

    def validate(self) -> None:
        """Validate critical configuration fields."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Please set GEMINI_API_KEY in your environment or in a .env file."
            )

config = AppConfig()
