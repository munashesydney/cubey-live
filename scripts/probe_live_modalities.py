"""
Probe which Gemini Live models support (AUDIO + TEXT) output modalities.

Each candidate is opened as a short-lived Live connection configured with
response_modalities=[AUDIO, TEXT]. Unsupported combinations fail immediately
with an API error (e.g. 1007); supported ones complete the setup handshake and
close. No audio or text is exchanged.

Usage:
    python scripts/probe_live_modalities.py
    python scripts/probe_live_modalities.py gemini-3.2-flash-live   # extra candidates

Note: every attempt is a real API handshake against your key.
"""

import asyncio
import sys
from pathlib import Path

# Make the project root importable when run as `python scripts/...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types

from src.config import config

# Models to test (the ones your key reports as bidiGenerateContent-capable).
# Extra candidates may be passed as command-line arguments.
CANDIDATE_MODELS = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-robotics-er-2-streaming-preview",
    "gemini-3.5-live-translate-preview",
]

# How long a single connection handshake may take before we give up on it.
CONNECT_TIMEOUT_SECONDS = 15.0


async def probe_model(client, model: str) -> str:
    """Attempt a short-lived (AUDIO, TEXT) Live connection; return a result line."""
    try:
        async def _connect() -> None:
            async with client.aio.live.connect(
                model=model,
                config=types.LiveConnectConfig(
                    response_modalities=[types.Modality.AUDIO, types.Modality.TEXT]
                ),
            ):
                return  # setup completed: the combination is supported

        await asyncio.wait_for(_connect(), timeout=CONNECT_TIMEOUT_SECONDS)
        return "OK (AUDIO+TEXT supported)"
    except asyncio.TimeoutError:
        return "TIMEOUT (handshake hung — likely unsupported or throttled)"
    except Exception as e:
        return f"FAIL: {type(e).__name__}: {str(e)[:140]}"


async def main() -> None:
    if not config.api_key:
        print("ERROR: GEMINI_API_KEY is not set (see src/config.py / .env).")
        sys.exit(1)

    models = CANDIDATE_MODELS + sys.argv[1:]
    if not models:
        print("ERROR: no models to probe.")
        sys.exit(1)

    client = genai.Client(
        api_key=config.api_key,
        http_options={"api_version": "v1alpha"},
    )
    print(f"Probing {len(models)} candidate(s) with AUDIO+TEXT output...\n")
    for model in models:
        print(f"  {model:<44} -> {await probe_model(client, model)}")
    print("\nDone. Any model that printed 'OK' supports TEXT modality.")


if __name__ == "__main__":
    asyncio.run(main())
