#!/usr/bin/env bash
# =============================================================================
# Run Native ROS 2 Jazzy Autonomous Navigation & SLAM on Cubey
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.pixi/bin:$PATH"

cd "$SCRIPT_DIR"
pixi run launch
