#!/usr/bin/env bash
# =============================================================================
# Run Native ROS 2 Jazzy Autonomous Navigation & SLAM on Cubey
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIXI_BIN="${HOME}/.pixi/bin/pixi"

if [[ ! -x "${PIXI_BIN}" ]]; then
    echo "Pixi is not installed at ${PIXI_BIN}" >&2
    exit 1
fi

cd "$SCRIPT_DIR"
exec "${PIXI_BIN}" run launch
