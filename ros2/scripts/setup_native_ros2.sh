#!/usr/bin/env bash
# =============================================================================
# Native ROS 2 Jazzy + Nav2 Setup Script for Cubey Robot (Raspberry Pi 5)
# Installs Pixi and resolves native aarch64 binary ROS 2 Jazzy dependencies.
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS2_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "========================================================="
echo " Setting up Native ROS 2 Jazzy + Nav2 on Raspberry Pi 5"
echo " Workspace: $ROS2_DIR"
echo "========================================================="

# 1. Ensure Pixi package manager is installed
if ! command -v pixi &> /dev/null; then
    echo ">> Pixi not found. Installing Pixi (https://pixi.sh)..."
    curl -fsSL https://pixi.sh/install.sh | bash
    export PATH="$HOME/.pixi/bin:$PATH"
fi

if ! command -v pixi &> /dev/null; then
    echo "Error: Pixi installation failed or not in PATH."
    exit 1
fi

echo ">> Pixi version: $(pixi --version)"

# 2. Add ~/.pixi/bin to ~/.bashrc if missing
if ! grep -q ".pixi/bin" "$HOME/.bashrc"; then
    echo 'export PATH="$HOME/.pixi/bin:$PATH"' >> "$HOME/.bashrc"
fi

# 3. Ensure UART and dialout permissions
echo ">> Checking user permissions for /dev/ttyUSB0 and /dev/ttyAMA0..."
sudo usermod -aG dialout "$USER" 2>/dev/null || true

# 4. Resolve and install native ROS 2 Jazzy environment
cd "$ROS2_DIR"
echo ">> Resolving and downloading RoboStack ROS 2 Jazzy binaries for aarch64..."
pixi install

echo "========================================================="
echo " Native ROS 2 Jazzy + Nav2 Environment Ready!"
echo " To start autonomous mapping & navigation:"
echo "   cd $ROS2_DIR && pixi run launch"
echo "========================================================="
