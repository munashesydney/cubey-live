#!/usr/bin/env bash
#
# run.sh — start Cubey using the project virtualenv.
# Run after scripts/startup/setup_pi.sh has created .venv.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ ! -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    echo "No virtualenv found at ${PROJECT_ROOT}/.venv" >&2
    echo "Run setup first: bash scripts/startup/setup_pi.sh" >&2
    exit 1
fi

exec "${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/main.py"
