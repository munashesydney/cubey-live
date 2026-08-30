/**
 * Cubey Web Portal — Shared Navigation & Global Telemetry Poller
 */

(function () {
  "use strict";

  const lblBatteryPct = document.getElementById("lbl-battery-pct");
  const lblLidarHz = document.getElementById("lbl-lidar-hz");
  const lblPoseText = document.getElementById("lbl-pose-text");
  const lblMappingState = document.getElementById("lbl-mapping-state");
  const pillStatus = document.getElementById("pill-status");

  async function pollGlobalStatus() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();

      if (lblBatteryPct && data.battery) {
        lblBatteryPct.textContent = `${data.battery.percentage}% ${data.battery.is_charging ? "⚡" : ""}`;
      }

      if (lblLidarHz && data.lidar) {
        lblLidarHz.textContent = `${(data.lidar.scan_rate_hz || 0).toFixed(1)} Hz`;
      }

      if (lblPoseText && data.mapping && data.mapping.pose) {
        const p = data.mapping.pose;
        lblPoseText.textContent = `X:${(p.x_m || 0).toFixed(1)}m Y:${(p.y_m || 0).toFixed(1)}m ${Math.round(p.theta_deg || 0)}°`;
      }

      if (lblMappingState && data.mapping) {
        if (data.mapping.is_mapping) {
          lblMappingState.textContent = "Mapping Active";
          if (pillStatus) pillStatus.querySelector(".dot")?.classList.add("live-dot");
        } else {
          lblMappingState.textContent = "Idle";
        }
      }
    } catch (_) {}
  }

  setInterval(pollGlobalStatus, 2000);
  pollGlobalStatus();
})();
