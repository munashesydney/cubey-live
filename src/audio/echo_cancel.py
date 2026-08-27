"""Runtime routing for PipeWire's WebRTC acoustic echo canceller."""

from dataclasses import dataclass
import logging
import os
import platform
import shutil
import subprocess
from typing import MutableMapping, Optional, Sequence

logger = logging.getLogger(__name__)


class EchoCancellationUnavailable(RuntimeError):
    """Raised when AEC was requested but its virtual audio graph is unavailable."""


@dataclass(frozen=True)
class EchoCancellationRouting:
    """Names required to route Cubey through the PipeWire AEC graph."""

    source_name: str
    sink_name: str
    host_device: str


def prepare_pipewire_echo_cancellation(
    source_name: str,
    sink_name: str,
    host_device: str = "pulse",
    *,
    environment: Optional[MutableMapping[str, str]] = None,
    system_name: Optional[str] = None,
    pactl_path: Optional[str] = None,
) -> EchoCancellationRouting:
    """Verify PipeWire AEC endpoints and route PulseAudio-compatible streams.

    PortAudio/sounddevice reaches PipeWire through its ALSA ``pulse`` device.
    The PulseAudio client environment variables select the AEC source/sink for
    Cubey without making them the global desktop defaults.
    """

    resolved_system = system_name or platform.system()
    if resolved_system != "Linux":
        raise EchoCancellationUnavailable(
            "PipeWire echo cancellation is only available in Cubey's Linux/Pi runtime"
        )

    pactl = pactl_path or shutil.which("pactl")
    if not pactl:
        raise EchoCancellationUnavailable(
            "pactl is unavailable; run scripts/audio/setup_pipewire_aec.sh on the Pi"
        )

    _require_pactl_object(pactl, "source", source_name)
    _require_pactl_object(pactl, "sink", sink_name)

    target_environment = environment if environment is not None else os.environ
    target_environment["PULSE_SOURCE"] = source_name
    target_environment["PULSE_SINK"] = sink_name

    existing_props = target_environment.get("PULSE_PROP", "").strip()
    cubey_props = "application.name=Cubey media.role=phone"
    target_environment["PULSE_PROP"] = (
        f"{existing_props} {cubey_props}".strip()
        if existing_props
        else cubey_props
    )

    logger.info(
        "PipeWire WebRTC AEC ready: capture='%s', playback='%s', host='%s'",
        source_name,
        sink_name,
        host_device,
    )
    return EchoCancellationRouting(source_name, sink_name, host_device)


def _require_pactl_object(pactl: str, kind: str, name: str) -> None:
    # pactl has no get-*-info command; volume lookup is a lightweight,
    # non-mutating existence check supported by Raspberry Pi OS Bookworm+.
    command: Sequence[str] = [pactl, f"get-{kind}-volume", name]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EchoCancellationUnavailable(
            f"Could not query PipeWire {kind} '{name}': {exc}"
        ) from exc

    if result.returncode == 0:
        return

    detail = (result.stderr or result.stdout or "not found").strip()
    raise EchoCancellationUnavailable(
        f"PipeWire AEC {kind} '{name}' is unavailable ({detail}). "
        "Run scripts/audio/setup_pipewire_aec.sh and restart Cubey."
    )
