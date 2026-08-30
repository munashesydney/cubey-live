#!/usr/bin/env bash
#
# setup_pi.sh — one-shot bootstrap for running Cubey on a fresh Raspberry Pi.
#
# Assumes the project folder is already on the Pi (scp/USB). Run as your
# normal desktop user (the one that logs into the screen), not root:
#
#     bash scripts/startup/setup_pi.sh
#
# Installs system packages, Python dependencies, .env, database migrations,
# pre-downloads the embedding and Local LLM models, and (by default)
# installs a systemd service so Cubey starts automatically at boot.
# Pass --no-autostart to skip the systemd service.
#
# Idempotent: safe to re-run at any time.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Locate the project root (this script lives in <project>/scripts/startup).
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if [[ "$(uname -m)" != "aarch64" && "$(uname -m)" != "arm64" ]]; then
    echo "ERROR: 64-bit Raspberry Pi OS is required (found: $(uname -m))." >&2
    exit 1
fi

if [[ $EUID -eq 0 ]]; then
    echo "ERROR: run this script as your normal desktop user, not root." >&2
    echo "The virtualenv and systemd service must belong to your login user." >&2
    exit 1
fi

if [[ ! -f "${PROJECT_ROOT}/requirements.txt" || ! -f "${PROJECT_ROOT}/main.py" ]]; then
    echo "ERROR: could not find the Cubey project at: ${PROJECT_ROOT}" >&2
    echo "Make sure the project folder is on the Pi and run:" >&2
    echo "  bash scripts/startup/setup_pi.sh" >&2
    exit 1
fi

echo "==> Cubey setup on $(hostname) ($(uname -m))"
echo "    Project root: ${PROJECT_ROOT}"

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
echo "==> Installing system packages (may take a while)..."
sudo apt-get update
sudo apt-get install -y \
    python3-venv python3-pip python3-tk \
    portaudio19-dev libasound2-dev \
    pipewire pipewire-bin pipewire-pulse wireplumber \
    libpipewire-0.3-modules libspa-0.2-modules \
    libasound2-plugins \
    libgomp1 \
    libjpeg-dev libpng-dev zlib1g-dev \
    cmake build-essential \
    alsa-utils ffmpeg

# ---------------------------------------------------------------------------
# Serial & USB Hardware Permissions (ESP32 UART & RPLIDAR C1 USB Serial)
# ---------------------------------------------------------------------------
echo "==> Configuring serial permissions for ESP32 UART and RPLIDAR C1 (/dev/ttyUSB*)..."
sudo usermod -a -G dialout,tty "${USER}" || true

# ---------------------------------------------------------------------------
# Python virtualenv + dependencies
# ---------------------------------------------------------------------------
VENV="${PROJECT_ROOT}/.venv"
if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "==> Creating Python virtualenv..."
    python3 -m venv "${VENV}"
fi

echo "==> Installing Python dependencies..."
"${VENV}/bin/pip" install --upgrade pip setuptools wheel
"${VENV}/bin/pip" install -r "${PROJECT_ROOT}/requirements.txt"
# openWakeWord's Linux metadata unconditionally pulls tflite-runtime, which
# has no Python 3.13/aarch64 wheel. The application uses ONNX exclusively and
# its runtime dependencies are pinned explicitly in requirements.txt.
"${VENV}/bin/pip" install --no-deps "openwakeword==0.6.0"

# ---------------------------------------------------------------------------
# .env (API key)
# ---------------------------------------------------------------------------
if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
    cp "${PROJECT_ROOT}/env.example" "${PROJECT_ROOT}/.env"
fi

if grep -q "GEMINI_API_KEY=your_gemini_api_key_here" "${PROJECT_ROOT}/.env"; then
    read -r -p "Enter your GEMINI_API_KEY (blank to skip, edit .env later): " KEY
    if [[ -n "${KEY}" ]]; then
        sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=${KEY}|" "${PROJECT_ROOT}/.env"
        echo "GEMINI_API_KEY written to .env"
    else
        echo "Skipped — set GEMINI_API_KEY in ${PROJECT_ROOT}/.env before first run."
    fi
fi
chmod 600 "${PROJECT_ROOT}/.env"

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------
echo "==> Running database migrations..."
(
    cd "${PROJECT_ROOT}"
    "${VENV}/bin/python" -m alembic upgrade head
)

# ---------------------------------------------------------------------------
# Pre-download embedding model (fastembed)
# ---------------------------------------------------------------------------
EMBEDDING_MODEL="${EMBEDDING_MODEL:-BAAI/bge-small-en-v1.5}"

echo "==> Pre-downloading embedding model (${EMBEDDING_MODEL})..."
"${VENV}/bin/python" - "${EMBEDDING_MODEL}" <<'PY'
import sys

from fastembed import TextEmbedding

TextEmbedding(sys.argv[1])
print("Embedding model ready.")
PY

# ---------------------------------------------------------------------------
# Pre-download Local LLM model (Qwen 2B GGUF)
# ---------------------------------------------------------------------------
echo "==> Pre-downloading Local LLM model..."
"${VENV}/bin/python" - "${PROJECT_ROOT}" <<'PY'
import sys
import os
from huggingface_hub import hf_hub_download

project_root = sys.argv[1]
repo_id = os.getenv("LOCAL_MODEL_REPO_ID", "bartowski/Qwen_Qwen3.5-2B-GGUF")
filename = os.getenv("LOCAL_MODEL_FILENAME", "Qwen_Qwen3.5-2B-Q4_K_M.gguf")
models_dir = os.path.join(project_root, "data", "models")

print(f"Downloading {filename} from {repo_id} to {models_dir} (this is ~1.4GB and will take some time)...")
hf_hub_download(repo_id=repo_id, filename=filename, local_dir=models_dir, local_dir_use_symlinks=False)
print("Local LLM model ready.")
PY

# ---------------------------------------------------------------------------
# Validate bundled custom openWakeWord model assets
# ---------------------------------------------------------------------------
echo "==> Validating bundled Cubey openWakeWord model..."
"${VENV}/bin/python" - "${PROJECT_ROOT}" <<'PY'
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
from openwakeword.model import Model  # noqa: F401

asset_dir = project_root / "src" / "assets" / "wakeword"
required = (
    asset_dir / "cubey_multigreeting_v2.onnx",
    asset_dir / "melspectrogram.onnx",
    asset_dir / "embedding_model.onnx",
)
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit("Missing bundled wake-word asset(s): " + ", ".join(missing))
print("Cubey openWakeWord runtime and model assets are ready.")
PY

# ---------------------------------------------------------------------------
# PipeWire / WebRTC acoustic echo cancellation
# ---------------------------------------------------------------------------
echo "==> Configuring PipeWire WebRTC acoustic echo cancellation..."
bash "${PROJECT_ROOT}/scripts/audio/setup_pipewire_aec.sh" \
    --env "${PROJECT_ROOT}/.env"

# ---------------------------------------------------------------------------
# Audio sanity check & microphone level boost
# ---------------------------------------------------------------------------
echo "==> Setting microphone hardware capture volume to 90%..."
amixer sset 'Capture' 90% 2>/dev/null || amixer sset 'Mic' 90% 2>/dev/null || true

echo "==> Detected audio devices (note the input/output indices):"
"${VENV}/bin/python" -c "import sounddevice as sd; print(sd.query_devices())" || true

# ---------------------------------------------------------------------------
# Boot autostart (systemd)
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--no-autostart" ]]; then
    echo "==> Skipping boot autostart (--no-autostart)."
else
    echo "==> Installing boot autostart (systemd service)..."
    sed -e "s|__USER__|${USER}|g" \
        -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
        -e "s|__VENV_PYTHON__|${VENV}/bin/python|g" \
        -e "s|__XDG_RUNTIME_DIR__|/run/user/$(id -u)|g" \
        -e "s|__DISPLAY__|:0|g" \
        "${SCRIPT_DIR}/cubey.service" \
        | tr -d '\r' \
        | sudo tee /etc/systemd/system/cubey.service > /dev/null

    sudo systemctl daemon-reload
    sudo systemctl enable cubey.service
    if sudo systemctl start cubey.service; then
        echo "==> Cubey autostart installed and started."
    else
        echo "Warning: cubey.service did not start cleanly. Logs: journalctl -u cubey -n 50" >&2
    fi
    echo "    Disable later with: sudo systemctl disable --now cubey"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo
echo "==> Setup complete!"
echo "    Manual start:  ${VENV}/bin/python ${PROJECT_ROOT}/main.py"
echo "    Or use:        bash ${SCRIPT_DIR}/run.sh"
echo
echo "    2D House Mapper & Remote Control Web App:"
echo "      http://$(hostname).local:8000  (Default: admin / cubey)"
echo
echo "    If audio picks the wrong device, configure it with:"
echo "      sudo raspi-config  (System Options > Audio)"
