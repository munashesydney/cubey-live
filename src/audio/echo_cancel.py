"""Runtime routing for PipeWire's WebRTC acoustic echo canceller."""

from dataclasses import dataclass
import json
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
    pw_dump_path: Optional[str] = None,
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

    pw_dump = pw_dump_path or shutil.which("pw-dump")
    if not pw_dump:
        raise EchoCancellationUnavailable(
            "pw-dump is unavailable; install pipewire-bin and run "
            "scripts/audio/setup_pipewire_aec.sh on the Pi"
        )

    graph_objects = _load_pipewire_graph(pw_dump)
    _require_pipewire_node(graph_objects, "source", source_name)
    _require_pipewire_node(graph_objects, "sink", sink_name)

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


def _load_pipewire_graph(pw_dump: str) -> list[dict]:
    command: Sequence[str] = [pw_dump, "--no-colors"]
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
            f"Could not inspect the PipeWire graph: {exc}"
        ) from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise EchoCancellationUnavailable(
            f"Could not inspect the PipeWire graph ({detail})"
        )

    try:
        graph_objects = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EchoCancellationUnavailable(
            "pw-dump returned invalid PipeWire graph data"
        ) from exc

    if not isinstance(graph_objects, list):
        raise EchoCancellationUnavailable("pw-dump returned an invalid PipeWire graph")
    return graph_objects


def _require_pipewire_node(graph_objects: list[dict], kind: str, name: str) -> None:
    expected_class = f"Audio/{kind.title()}"
    for graph_object in graph_objects:
        props = graph_object.get("info", {}).get("props", {})
        if (
            props.get("node.name") == name
            and props.get("media.class") == expected_class
        ):
            return

    raise EchoCancellationUnavailable(
        f"PipeWire AEC {kind} '{name}' is unavailable. "
        "Run scripts/audio/setup_pipewire_aec.sh and restart Cubey."
    )
