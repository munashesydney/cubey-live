/**
 * Cubey Portal — Home Dashboard Live Telemetry Script
 */

(function () {
  "use strict";

  const statBattery = document.getElementById("stat-battery");
  const statLidar = document.getElementById("stat-lidar");
  const statMapCells = document.getElementById("stat-map-cells");

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

        // 1. Hero Stat Badges
        if (statBattery && sys.battery) {
          const pct = sys.battery.percentage;
          statBattery.textContent = `${pct}% ${sys.battery.is_charging ? "⚡" : ""}`;
          if (pct <= 20) {
            statBattery.style.color = "#F38BA8"; // Low battery red
          } else if (pct <= 40) {
            statBattery.style.color = "#F9E2AF"; // Medium yellow
          } else {
            statBattery.style.color = "#A6E3A1"; // Good green
          }
        }

        if (statLidar && sys.lidar) {
          const hz = sys.lidar.scan_rate_hz || 0;
          statLidar.textContent = `${hz.toFixed(1)} Hz`;
          statLidar.style.color = sys.lidar.is_scanning ? "#74C7EC" : "#6C7086";
        }

        if (statMapCells && sys.mapping) {
          const cells = sys.mapping.explored_cells || 0;
          statMapCells.textContent = cells.toLocaleString();
        }

        // 2. Hardware Health Strip
        if (valBatteryVolts && sys.battery) {
          const v = sys.battery.voltage || 0;
          valBatteryVolts.textContent = `${v.toFixed(2)} V`;
        }

        if (valMotionState) {
          valMotionState.textContent = sys.motion || "STOPPED";
        }

        if (valFrontClearance && sys.lidar) {
          const d = sys.lidar.min_front_mm;
          if (d && d > 0 && d < 6000) {
            valFrontClearance.textContent = `${d} mm`;
            if (d < 150) {
              valFrontClearance.style.color = "#F38BA8";
            } else if (d < 300) {
              valFrontClearance.style.color = "#FAB387";
            } else {
              valFrontClearance.style.color = "#CDD6F4";
            }
          } else {
            valFrontClearance.textContent = "Clear (>2m)";
            valFrontClearance.style.color = "#A6E3A1";
          }
        }
      }

      if (audioRes.ok) {
        const audio = await audioRes.json();
        if (valAudioSpec) {
          const dev = audio.device_name || "PipeWire AEC";
          valAudioSpec.textContent = `${dev} (${audio.sample_rate || 16000} Hz)`;
        }
      }
    } catch (err) {
      console.debug("Dashboard update error:", err);
    }
  }

  setInterval(updateDashboard, 1500);
  updateDashboard();
})();
