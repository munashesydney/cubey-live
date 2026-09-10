#!/usr/bin/env bash
# =============================================================================
# Install a pinned Arduino build toolchain for the Cubey Wheels ESP32 firmware.
#
# Run this once on the Pi, then firmware updates no longer need a workstation:
#
#   sudo -E python3 scripts/firmware/update_esp_via_cubey_wifi.py --compile
#
# The core and library versions are pinned to the toolchain that produced the
# last verified image. The sketch subclasses Adafruit_BNO08x and touches its
# HAL internals, which are not API-stable across library releases.
#
# The toolchain lives under /opt so it is visible to both the cubey user and
# root, which the Wi-Fi handoff in the updater requires.
# =============================================================================
set -euo pipefail

ARDUINO_CLI_VERSION="${ARDUINO_CLI_VERSION:-1.5.1}"
ESP32_CORE_VERSION="${ESP32_CORE_VERSION:-3.3.11}"
ESP32_INDEX="https://espressif.github.io/arduino-esp32/package_esp32_index.json"

TOOLCHAIN_ROOT="/opt/cubey-arduino"
BIN_DIR="${TOOLCHAIN_ROOT}/bin"
CONFIG_FILE="${TOOLCHAIN_ROOT}/arduino-cli.yaml"

# "Library name@version" pairs, pinned to the verified build. The sketch does
# not use Adafruit_GFX/SSD1306, so the dependencies Adafruit_VL53L0X declares
# are resolved automatically rather than pinned here.
LIBRARIES=(
    "Adafruit BNO08x@1.2.7"
    "Adafruit BusIO@1.17.4"
    "Adafruit_VL53L0X@1.2.5"
    "Adafruit Unified Sensor@1.1.15"
)

if [[ ${EUID} -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKETCH_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)/cubey_wheels"

echo "==> Installing arduino-cli ${ARDUINO_CLI_VERSION} into ${BIN_DIR}"
install -d "${BIN_DIR}"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT
archive="${work}/arduino-cli.tar.gz"
curl -fsSL -o "${archive}" \
    "https://github.com/arduino/arduino-cli/releases/download/v${ARDUINO_CLI_VERSION}/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_ARM64.tar.gz"
tar -xzf "${archive}" -C "${work}" arduino-cli
install -m 0755 "${work}/arduino-cli" "${BIN_DIR}/arduino-cli"

echo "==> Writing ${CONFIG_FILE}"
install -d "${TOOLCHAIN_ROOT}"
cat > "${CONFIG_FILE}" <<EOF
directories:
  data: ${TOOLCHAIN_ROOT}/data
  downloads: ${TOOLCHAIN_ROOT}/downloads
  user: ${TOOLCHAIN_ROOT}/user
board_manager:
  additional_urls:
    - ${ESP32_INDEX}
EOF

cli=("${BIN_DIR}/arduino-cli" --config-file "${CONFIG_FILE}")

echo "==> Updating package index"
"${cli[@]}" core update-index

echo "==> Installing ESP32 core ${ESP32_CORE_VERSION}"
"${cli[@]}" core install "esp32:esp32@${ESP32_CORE_VERSION}"

echo "==> Installing pinned libraries"
for library in "${LIBRARIES[@]}"; do
    "${cli[@]}" lib install "${library}"
done

echo "==> Verifying the pinned toolchain builds cubey_wheels"
"${cli[@]}" compile --fqbn esp32:esp32:esp32s3 "${SKETCH_DIR}"

echo "==> Toolchain ready: ${BIN_DIR}/arduino-cli"
