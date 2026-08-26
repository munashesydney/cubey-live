"""Audio device selection and hardware capability probing (WASAPI, ALSA, I2S)."""

from dataclasses import dataclass
import logging
import platform
from typing import Optional, Tuple, Union

import sounddevice as sd

logger = logging.getLogger(__name__)

DeviceId = Optional[Union[int, str]]


@dataclass(frozen=True)
class AudioDeviceSelection:
    device: DeviceId
    sample_rate: int
    channels: int
    dtype: str
    name: str
    host_api: str


def select_audio_device(
    kind: str,
    desired_sample_rate: int,
    explicit_device: Optional[str] = None,
    prefer_low_latency: bool = True,
    explicit_sample_rate: int = 0,
    explicit_channels: int = 0,
    explicit_dtype: str = "",
) -> AudioDeviceSelection:
    """Resolve an input/output endpoint, rate, channel count, and bit depth.

    Supports native low-latency Windows endpoints (WASAPI 48kHz) as well as
    Raspberry Pi / Linux ALSA I2S hardware (MAX98357A DAC & INMP441 mics
    requiring 48kHz stereo int32 / S32_LE).
    """
    if kind not in {"input", "output"}:
        raise ValueError("kind must be 'input' or 'output'")

    device: DeviceId = _resolve_device_id(explicit_device, kind)
    if (
        device is None
        and prefer_low_latency
        and platform.system() == "Windows"
    ):
        device = _windows_wasapi_default(kind)

    # Defaults if no device info available
    default_rate = explicit_sample_rate if explicit_sample_rate > 0 else desired_sample_rate
    default_channels = explicit_channels if explicit_channels > 0 else 1
    default_dtype = explicit_dtype if explicit_dtype else "int16"

    if device is None:
        return AudioDeviceSelection(
            device=None,
            sample_rate=default_rate,
            channels=default_channels,
            dtype=default_dtype,
            name="system default",
            host_api="default",
        )

    try:
        info = sd.query_devices(device=device, kind=kind)
        host_api = sd.query_hostapis(info["hostapi"])["name"]
        
        # If user explicitly specified hardware parameters, honor them
        if explicit_sample_rate > 0 and explicit_channels > 0 and explicit_dtype:
            return AudioDeviceSelection(
                device=device,
                sample_rate=explicit_sample_rate,
                channels=explicit_channels,
                dtype=explicit_dtype,
                name=info.get("name", str(device)),
                host_api=host_api,
            )

        stream_rate, stream_channels, stream_dtype = _resolve_device_settings(
            device=device,
            kind=kind,
            desired_rate=desired_sample_rate,
            info=info,
            explicit_rate=explicit_sample_rate,
            explicit_channels=explicit_channels,
            explicit_dtype=explicit_dtype,
        )

        selection = AudioDeviceSelection(
            device=device,
            sample_rate=stream_rate,
            channels=stream_channels,
            dtype=stream_dtype,
            name=info.get("name", str(device)),
            host_api=host_api,
        )
        logger.info(
            "Selected %s device '%s' (id=%r) via %s: %d Hz, %d ch, %s",
            kind,
            selection.name,
            selection.device,
            selection.host_api,
            selection.sample_rate,
            selection.channels,
            selection.dtype,
        )
        return selection
    except Exception as exc:
        logger.warning(
            "Could not query requested %s device %r (%s); using configured defaults",
            kind,
            device,
            exc,
        )
        return AudioDeviceSelection(
            device=None,
            sample_rate=default_rate,
            channels=default_channels,
            dtype=default_dtype,
            name="system default",
            host_api="default",
        )


def _resolve_device_id(value: Optional[str], kind: str) -> DeviceId:
    """Resolve an explicit device string (index, name, plughw:2,0, hw:2,0) to a PortAudio device."""
    if value is None or not str(value).strip():
        return None

    stripped = str(value).strip()

    # 1. Integer index (e.g. "2" or 2)
    try:
        return int(stripped)
    except ValueError:
        pass

    # 2. Try direct query to see if sounddevice recognizes the name as-is
    try:
        sd.query_devices(device=stripped, kind=kind)
        return stripped
    except Exception:
        pass

    # 3. Smart ALSA / hardware string parsing (e.g. "plughw:2,0", "hw:2,0", "card 2")
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"

    import re
    card_match = re.search(r"(?:plughw|hw|card)[:,\s]*(\d+)", stripped, re.IGNORECASE)
    card_num = card_match.group(1) if card_match else None

    devices = []
    try:
        devices = sd.query_devices()
    except Exception:
        pass

    # Priority A: Check for hw:X,Y or hw:X in device names
    if card_num is not None:
        for idx, dev in enumerate(devices):
            name = dev.get("name", "")
            max_ch = dev.get(channel_key, 0)
            if max_ch > 0:
                if f"hw:{card_num}" in name.lower() or f"card={card_num}" in name.lower() or f"({card_num}," in name:
                    logger.info("Resolved audio device '%s' -> PortAudio index %d: '%s'", stripped, idx, name)
                    return idx

    # Priority B: Case-insensitive substring match against device names
    clean_query = stripped.lower().replace("plughw:", "").replace("hw:", "").strip()
    for idx, dev in enumerate(devices):
        name = dev.get("name", "")
        max_ch = dev.get(channel_key, 0)
        if max_ch > 0 and clean_query and clean_query in name.lower():
            logger.info("Resolved audio device '%s' -> PortAudio index %d: '%s'", stripped, idx, name)
            return idx

    # Priority C: If card_num is integer index within range
    if card_num is not None:
        try:
            c_idx = int(card_num)
            if 0 <= c_idx < len(devices):
                if devices[c_idx].get(channel_key, 0) > 0:
                    logger.info("Using device index %d from '%s'", c_idx, stripped)
                    return c_idx
        except Exception:
            pass

    logger.warning("Could not match audio device '%s' for %s; using default", stripped, kind)
    return None


def _windows_wasapi_default(kind: str) -> Optional[int]:
    field = f"default_{kind}_device"
    for host_api in sd.query_hostapis():
        if "WASAPI" in host_api["name"].upper():
            index = int(host_api[field])
            return index if index >= 0 else None
    return None


def _resolve_device_settings(
    device: DeviceId,
    kind: str,
    desired_rate: int,
    info: dict,
    explicit_rate: int = 0,
    explicit_channels: int = 0,
    explicit_dtype: str = "",
) -> Tuple[int, int, str]:
    """Probe hardware to find supported sample rate, channels, and bit depth."""
    checker = sd.check_input_settings if kind == "input" else sd.check_output_settings
    native_rate = int(round(float(info.get("default_samplerate", 48000))))

    # Candidates to probe in order of preference
    candidates = []

    # If partial explicit settings were provided
    if explicit_rate or explicit_channels or explicit_dtype:
        r = explicit_rate or native_rate or desired_rate
        c = explicit_channels or (1 if kind == "input" else 2)
        d = explicit_dtype or "int16"
        candidates.append((r, c, d))

    # Standard combinations to probe
    candidates.extend([
        (desired_rate, 1, "int16"),
        (native_rate, 1, "int16"),
        (48000, 2, "int32"),   # Raspberry Pi I2S DAC (MAX98357A / INMP441)
        (48000, 2, "int16"),
        (44100, 2, "int16"),
        (native_rate, 2, "int32"),
        (native_rate, 2, "int16"),
    ])

    for rate, ch, dt in candidates:
        try:
            checker(device=device, samplerate=rate, channels=ch, dtype=dt)
            return rate, ch, dt
        except Exception:
            continue

    # Fallback to explicit or safe defaults
    final_rate = explicit_rate or native_rate or desired_rate
    final_ch = explicit_channels or (2 if platform.system() == "Linux" else 1)
    final_dt = explicit_dtype or ("int32" if platform.system() == "Linux" else "int16")
    return final_rate, final_ch, final_dt
