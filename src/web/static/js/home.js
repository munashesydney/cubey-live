/**
 * Cubey Portal — Home Dashboard Script
 */

(function () {
  "use strict";

  const statBattery = document.getElementById("stat-battery");
  const statLidar = document.getElementById("stat-lidar");
  const statMapCells = document.getElementById("stat-map-cells");
  const statActiveMap = document.getElementById("stat-active-map");

  const valBatteryVolts = document.getElementById("val-battery-volts");
  const valMotionState = document.getElementById("val-motion-state");
  const valFrontClearance = document.getElementById("val-front-clearance");
  const valAudioSpec = document.getElementById("val-audio-spec");

  async function updateDashboard() {
    try {
      const [sysRes, audioRes] = await Promise.all([
        fetch("/api/status"),
        fetch("/api/audio/status"),
      ]);

      if (sysRes.ok) {
        const sys = await sysRes.json();
        if (statBattery && sys.battery) {
          statBattery.textContent = `${sys.battery.percentage}%`;
        }
        if (statLidar && sys.lidar) {
          statLidar.textContent = `${(sys.lidar.scan_rate_hz || 0).toFixed(1)} Hz`;
        }
        if (statMapCells && sys.mapping) {
          statMapCells.textContent = `${sys.mapping.explored_cells || 0}`;
        }
        if (statActiveMap && sys.mapping) {
          statActiveMap.textContent = sys.mapping.map_name || "None";
        }
        if (valBatteryVolts && sys.battery) {
          valBatteryVolts.textContent = `${(sys.battery.voltage || 0).toFixed(2)}V`;
        }
        if (valMotionState) {
          valMotionState.textContent = sys.motion || "STOPPED";
        }
        if (valFrontClearance && sys.lidar) {
          const front = sys.lidar.min_front_mm;
          valFrontClearance.textContent = front && front < 9000 ? `${front} mm` : "Clear (>2m)";
        }
      }

      if (audioRes.ok) {
        const audio = await audioRes.json();
        if (valAudioSpec) {
          valAudioSpec.textContent = `${audio.device_name || "PipeWire AEC"} (${audio.sample_rate || 16000}Hz)`;
        }
      }
    } catch (err) {
      console.debug("Dashboard update error:", err);
    }
  }

  setInterval(updateDashboard, 2000);
  updateDashboard();
})();
