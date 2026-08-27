#!/usr/bin/env bash
# Install Cubey's persistent PipeWire/WebRTC acoustic echo-cancellation graph.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE_CONFIG="${SCRIPT_DIR}/60-cubey-echo-cancel.conf"
TARGET_CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/pipewire/pipewire.conf.d"
TARGET_CONFIG="${TARGET_CONFIG_DIR}/60-cubey-echo-cancel.conf"
ENV_FILE="${PROJECT_ROOT}/.env"

if [[ ${EUID} -eq 0 ]]; then
    echo "ERROR: run this script as Cubey's normal desktop user, not root." >&2
    exit 1
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --env requires a path" >&2
                exit 2
            fi
            ENV_FILE="$2"
            shift 2
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

if ! command -v systemctl >/dev/null 2>&1; then
    echo "ERROR: systemctl is missing; Raspberry Pi OS with systemd is required." >&2
    exit 1
fi

if ! command -v pactl >/dev/null 2>&1; then
    echo "ERROR: pactl is missing (the pulseaudio-utils package was not installed)." >&2
    echo "Install it with: sudo apt-get install -y pulseaudio-utils" >&2
    exit 1
fi

if [[ ! -f "${SOURCE_CONFIG}" ]]; then
    echo "ERROR: PipeWire configuration template is missing: ${SOURCE_CONFIG}" >&2
    exit 1
fi

mkdir -p "${TARGET_CONFIG_DIR}"
install -m 0644 "${SOURCE_CONFIG}" "${TARGET_CONFIG}"

echo "==> Installed PipeWire WebRTC AEC configuration: ${TARGET_CONFIG}"
echo "==> Restarting the user audio graph..."
systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service

source_ready=false
sink_ready=false
for _ in $(seq 1 40); do
    if pactl get-source-volume cubey_echo_cancel_source >/dev/null 2>&1; then
        source_ready=true
    fi
    if pactl get-sink-volume cubey_echo_cancel_sink >/dev/null 2>&1; then
        sink_ready=true
    fi
    if [[ ${source_ready} == true && ${sink_ready} == true ]]; then
        break
    fi
    sleep 0.25
done

if [[ ${source_ready} != true || ${sink_ready} != true ]]; then
    echo "ERROR: PipeWire restarted but Cubey's AEC endpoints did not appear." >&2
    echo "Check: journalctl --user -u pipewire -n 80" >&2
    exit 1
fi

set_env_value() {
    local key="$1"
    local value="$2"
    if [[ -f "${ENV_FILE}" ]] && grep -q "^${key}=" "${ENV_FILE}"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
        printf '\n%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
    fi
}

set_env_value AUDIO_ENABLE_ECHO_CANCELLATION true
set_env_value AUDIO_ECHO_CANCEL_SOURCE cubey_echo_cancel_source
set_env_value AUDIO_ECHO_CANCEL_SINK cubey_echo_cancel_sink
set_env_value AUDIO_ECHO_CANCEL_HOST_DEVICE pulse

echo "==> Enabled Cubey AEC in ${ENV_FILE}"
echo "==> Verified source: cubey_echo_cancel_source"
echo "==> Verified sink:   cubey_echo_cancel_sink"
echo "Restart Cubey to begin using WebRTC acoustic echo cancellation."
