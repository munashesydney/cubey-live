"""Audio device selection that prefers native low-latency Windows endpoints."""

from dataclasses import dataclass
import logging
import platform
from typing import Optional, Union

import sounddevice as sd

logger = logging.getLogger(__name__)

DeviceId = Optional[Union[int, str]]


@dataclass(frozen=True)
class AudioDeviceSelection:
    device: DeviceId
    sample_rate: int
    name: str
    host_api: str


def select_audio_device(
    kind: str,
    desired_sample_rate: int,
    explicit_device: Optional[str] = None,
    prefer_low_latency: bool = True,
) -> AudioDeviceSelection:
    """Resolve an input/output endpoint and a rate it supports natively.

    PortAudio's Windows MME defaults add roughly 90 ms in each direction on
    common hardware. WASAPI shared endpoints report about 3 ms, but generally
    require their native mix rate (usually 48 kHz), so the audio classes
    resample at the application boundary.
    """
    if kind not in {"input", "output"}:
        raise ValueError("kind must be 'input' or 'output'")

    device: DeviceId = _parse_device(explicit_device)
    if (
        device is None
        and prefer_low_latency
        and platform.system() == "Windows"
    ):
        device = _windows_wasapi_default(kind)

    if device is None:
        return AudioDeviceSelection(None, desired_sample_rate, "system default", "default")

    try:
        info = sd.query_devices(device=device, kind=kind)
        host_api = sd.query_hostapis(info["hostapi"])["name"]
        stream_rate = _supported_rate(device, kind, desired_sample_rate, info)
        selection = AudioDeviceSelection(
            device=device,
            sample_rate=stream_rate,
            name=info["name"],
            host_api=host_api,
        )
        logger.info(
            "Selected %s device '%s' via %s at %d Hz",
            kind,
            selection.name,
            selection.host_api,
            selection.sample_rate,
        )
        return selection
    except Exception as exc:
        logger.warning(
            "Could not use requested low-latency %s device %r (%s); "
            "falling back to the system default",
            kind,
            device,
            exc,
        )
        return AudioDeviceSelection(None, desired_sample_rate, "system default", "default")


def _parse_device(value: Optional[str]) -> DeviceId:
    if value is None or not value.strip():
        return None
    stripped = value.strip()
    try:
        return int(stripped)
    except ValueError:
        return stripped


def _windows_wasapi_default(kind: str) -> Optional[int]:
    field = f"default_{kind}_device"
    for host_api in sd.query_hostapis():
        if "WASAPI" in host_api["name"].upper():
            index = int(host_api[field])
            return index if index >= 0 else None
    return None


def _supported_rate(device: DeviceId, kind: str, desired: int, info) -> int:
    checker = sd.check_input_settings if kind == "input" else sd.check_output_settings
    kwargs = {
        "device": device,
        "channels": 1,
        "dtype": "int16",
    }
    try:
        checker(samplerate=desired, **kwargs)
        return desired
    except Exception:
        native = int(round(float(info["default_samplerate"])))
        checker(samplerate=native, **kwargs)
        return native
