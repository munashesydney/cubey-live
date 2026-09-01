#!/usr/bin/env bash
# Install/restart the checked-in Cubey app and native Nav2 systemd services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV="${PROJECT_ROOT}/.venv"
PIXI_BIN="${HOME}/.pixi/bin/pixi"

if [[ ${EUID} -eq 0 ]]; then
    echo "Run this script as the cubey desktop user, not root." >&2
    exit 1
fi

if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "Missing application virtualenv at ${VENV}." >&2
    exit 1
fi

if [[ ! -x "${PIXI_BIN}" ]]; then
    echo "Missing Pixi ROS runtime at ${PIXI_BIN}." >&2
    exit 1
fi

echo "==> Installing native Nav2 and Cubey application services"
sudo systemctl stop cubey.service cubey-nav2.service 2>/dev/null || true

sed -e "s|__USER__|${USER}|g" \
    -e "s|__HOME__|${HOME}|g" \
    -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
    "${SCRIPT_DIR}/cubey-nav2.service" \
    | tr -d '\r' \
    | sudo tee /etc/systemd/system/cubey-nav2.service > /dev/null

sed -e "s|__USER__|${USER}|g" \
    -e "s|__PROJECT_ROOT__|${PROJECT_ROOT}|g" \
    -e "s|__VENV_PYTHON__|${VENV}/bin/python|g" \
    -e "s|__XDG_RUNTIME_DIR__|/run/user/$(id -u)|g" \
    -e "s|__DISPLAY__|:0|g" \
    "${SCRIPT_DIR}/cubey.service" \
    | tr -d '\r' \
    | sudo tee /etc/systemd/system/cubey.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable cubey-nav2.service cubey.service
sudo systemctl start cubey-nav2.service
sudo systemctl start cubey.service

echo "==> Services started"
sudo systemctl --no-pager --full status cubey-nav2.service cubey.service
