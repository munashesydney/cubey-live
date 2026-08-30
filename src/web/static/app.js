/**
 * Cubey 2D House Mapper & Remote Control Client Application.
 *
 * Handles WebSocket streaming, zlib decompression of occupancy grids,
 * smooth 2D Canvas pan/zoom rendering, virtual touch joystick, keyboard driving,
 * and SQLite floorplan management.
 */

(function () {
  "use strict";

  // State
  let ws = null;
  let isConnected = false;
  let isMapping = false;
  let activeMapName = "Live Floorplan";

  let robotPose = { x_m: 0.0, y_m: 0.0, theta_deg: 0.0 };
  let trajectory = [];
  let laserScan = [];

  // Occupancy Grid Metadata & Buffer
  let gridWidth = 400;
  let gridHeight = 400;
  let resolutionCm = 5.0;
  let resolutionM = 0.05;
  let originXM = -10.0;
  let originYM = -10.0;
  let gridBuffer = new Int8Array(400 * 400).fill(-1);

  // Viewport Transform (Canvas Pan & Zoom)
  let viewScale = 30.0; // pixels per meter
  let viewPanX = 0.0;   // canvas pixel offset
  let viewPanY = 0.0;
  let isDragging = false;
  let dragStartX = 0;
  let dragStartY = 0;

  let currentSpeed = 180;

  // DOM Elements
  const canvas = document.getElementById("map-canvas");
  const ctx = canvas.getContext("2d", { alpha: false });
  const viewport = document.getElementById("viewport");

  const lblActiveMapName = document.getElementById("lbl-active-map-name");
  const lblMappingState = document.getElementById("lbl-mapping-state");
  const pillStatus = document.getElementById("pill-status");
  const lblBatteryPct = document.getElementById("lbl-battery-pct");
  const lblLidarHz = document.getElementById("lbl-lidar-hz");
  const lblPoseText = document.getElementById("lbl-pose-text");
  const btnToggleMapping = document.getElementById("btn-toggle-mapping");
  const btnMappingText = document.getElementById("btn-mapping-text");

  const btnSaveMap = document.getElementById("btn-save-map");
  const btnOpenLibrary = document.getElementById("btn-open-library");
  const btnResetMap = document.getElementById("btn-reset-map");
  const btnRecenter = document.getElementById("btn-recenter");
  const btnZoomIn = document.getElementById("btn-zoom-in");
  const btnZoomOut = document.getElementById("btn-zoom-out");
  const btnExportPng = document.getElementById("btn-export-png");

  const speedSlider = document.getElementById("speed-slider");
  const lblSpeedVal = document.getElementById("lbl-speed-val");

  // Modals
  const modalModeSelect = document.getElementById("modal-mode-select");
  const btnCloseModeModal = document.getElementById("btn-close-mode-modal");
  const btnStartAuto = document.getElementById("btn-start-auto");
  const btnStartManual = document.getElementById("btn-start-manual");

  const modalSave = document.getElementById("modal-save");
  const inputMapName = document.getElementById("input-map-name");
  const btnConfirmSave = document.getElementById("btn-confirm-save");
  const btnCancelSave = document.getElementById("btn-cancel-save");

  const modalLibrary = document.getElementById("modal-library");
  const btnCloseLibrary = document.getElementById("btn-close-library");
  const mapsListContainer = document.getElementById("maps-list-container");


  // Virtual Joystick Elements
  const joystickContainer = document.getElementById("virtual-joystick");
  const joystickKnob = document.getElementById("joystick-knob");

  // ------------------------------------------------------------------------
  // Canvas Setup & Resize
  // ------------------------------------------------------------------------

  function resizeCanvas() {
    canvas.width = viewport.clientWidth * window.devicePixelRatio;
    canvas.height = viewport.clientHeight * window.devicePixelRatio;
    ctx.imageSmoothingEnabled = false;

    if (viewPanX === 0 && viewPanY === 0) {
      viewPanX = canvas.width / 2;
      viewPanY = canvas.height / 2;
    }
    render();
  }

  window.addEventListener("resize", resizeCanvas);

  // ------------------------------------------------------------------------
  // WebSocket Live Stream Connection
  // ------------------------------------------------------------------------

  async function connectWebSocket() {
    let token = "";
    try {
      const res = await fetch("/api/auth/token");
      if (res.ok) {
        const data = await res.json();
        token = data.token || "";
      }
    } catch (e) {
      console.warn("Could not fetch auth token:", e);
    }

    const loc = window.location;
    const proto = loc.protocol === "https:" ? "wss:" : "ws:";
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
    const wsUrl = `${proto}//${loc.host}/ws/live_map${tokenParam}`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      isConnected = true;
      console.log("Connected to Cubey Live Map WebSocket");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "map_update") {
          handleMapUpdate(msg);
        }
      } catch (err) {
        console.error("Error parsing WebSocket message:", err);
      }
    };

    ws.onclose = () => {
      isConnected = false;
      pillStatus.classList.remove("active");
      lblMappingState.textContent = "Disconnected (Reconnecting...)";
      setTimeout(connectWebSocket, 1500);
    };

    ws.onerror = (err) => {
      console.warn("WebSocket error:", err);
    };
  }

  function handleMapUpdate(data) {
    robotPose = data.pose || robotPose;
    trajectory = data.trajectory || trajectory;
    laserScan = data.laser_scan || laserScan;

    gridWidth = data.width || gridWidth;
    gridHeight = data.height || gridHeight;
    resolutionCm = data.resolution_cm || resolutionCm;
    resolutionM = resolutionCm / 100.0;
    originXM = data.origin_x_m || originXM;
    originYM = data.origin_y_m || originYM;

    isMapping = data.is_mapping;
    activeMapName = data.map_name || activeMapName;

    // Decompress occupancy grid if provided in this frame
    if (data.grid_compressed_b64 && window.pako) {
      try {
        const binaryStr = atob(data.grid_compressed_b64);
        const bytes = new Uint8Array(binaryStr.length);
        for (let i = 0; i < binaryStr.length; i++) {
          bytes[i] = binaryStr.charCodeAt(i);
        }
        const decompressed = window.pako.inflate(bytes);
        gridBuffer = new Int8Array(decompressed.buffer);
      } catch (err) {
        console.error("Decompression error:", err);
      }
    }

    // Update Header Badges
    const navState = data.nav_state || "IDLE";
    const navMode = data.nav_mode || "manual";

    lblActiveMapName.textContent = activeMapName;
    if (isMapping || navState === "EXPLORING" || navState === "NAVIGATING") {
      pillStatus.classList.add("active");
      if (navState === "EXPLORING" || navMode === "autonomous") {
        lblMappingState.textContent = "🤖 Auto Mapping";
      } else if (navState === "NAVIGATING") {
        lblMappingState.textContent = "🎯 Navigating Goal";
      } else {
        lblMappingState.textContent = "🕹️ Manual Mapping";
      }
      btnMappingText.textContent = "Stop Mapping";
      btnToggleMapping.className = "btn btn-danger-outline";
    } else {
      pillStatus.classList.remove("active");
      lblMappingState.textContent = "Mapping Idle";
      btnMappingText.textContent = "Start Mapping";
      btnToggleMapping.className = "btn btn-primary";
    }

    if (data.battery_pct !== undefined) {
      lblBatteryPct.textContent = `${data.battery_pct}%`;
    }
    if (data.lidar_rate_hz !== undefined) {
      lblLidarHz.textContent = `${data.lidar_rate_hz.toFixed(1)} Hz`;
    }

    lblPoseText.textContent = `X: ${robotPose.x_m.toFixed(2)}m Y: ${robotPose.y_m.toFixed(2)}m ${robotPose.theta_deg.toFixed(0)}°`;

    render();
  }


  // ------------------------------------------------------------------------
  // High-Performance 2D Canvas Renderer
  // ------------------------------------------------------------------------

  // Offscreen bitmap canvas for fast pixel grid stamping
  const offscreenCanvas = document.createElement("canvas");
  const offscreenCtx = offscreenCanvas.getContext("2d");

  function render() {
    const w = canvas.width;
    const h = canvas.height;

    // 1. Fill base dark background
    ctx.fillStyle = "#11111B";
    ctx.fillRect(0, 0, w, h);

    // Save transform
    ctx.save();
    ctx.translate(viewPanX, viewPanY);

    // 2. Draw 2D Occupancy Grid Bitmap
    if (gridWidth > 0 && gridHeight > 0 && gridBuffer.length === gridWidth * gridHeight) {
      if (offscreenCanvas.width !== gridWidth || offscreenCanvas.height !== gridHeight) {
        offscreenCanvas.width = gridWidth;
        offscreenCanvas.height = gridHeight;
      }

      const imgData = offscreenCtx.createImageData(gridWidth, gridHeight);
      const data = imgData.data;

      // Color mapping:
      // -1 (Unknown)  -> Transparent / Dark (#11111B)
      //  0 (Free)     -> Soft dark blue-gray (#2E3440 / 45, 52, 64)
      // 100 (Occupied)-> Neon Cyan (#89DCEB / 137, 220, 235)
      for (let y = 0; y < gridHeight; y++) {
        for (let x = 0; x < gridWidth; x++) {
          const idx = y * gridWidth + x;
          const val = gridBuffer[idx];
          const pIdx = ((gridHeight - 1 - y) * gridWidth + x) * 4; // Flip Y for Cartesian up

          if (val === 100) {
            // Wall / Obstacle
            data[pIdx] = 137;     // R
            data[pIdx + 1] = 220; // G
            data[pIdx + 2] = 235; // B
            data[pIdx + 3] = 255; // Alpha
          } else if (val === 0) {
            // Free walkable corridor
            data[pIdx] = 49;      // #313244
            data[pIdx + 1] = 50;
            data[pIdx + 2] = 68;
            data[pIdx + 3] = 220;
          } else {
            // Unknown / Unexplored
            data[pIdx] = 0;
            data[pIdx + 1] = 0;
            data[pIdx + 2] = 0;
            data[pIdx + 3] = 0;
          }
        }
      }

      offscreenCtx.putImageData(imgData, 0, 0);

      // Render offscreen canvas scaled to real-world meters
      const gridPixelW = gridWidth * resolutionM * viewScale;
      const gridPixelH = gridHeight * resolutionM * viewScale;
      const originPixelX = originXM * viewScale;
      const originPixelY = -originYM * viewScale - gridPixelH;

      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(offscreenCanvas, originPixelX, originPixelY, gridPixelW, gridPixelH);
    }

    // 3. Draw Metric Coordinate Grid Overlay (Subtle 1-meter and 5-meter grid lines)
    ctx.strokeStyle = "rgba(49, 50, 68, 0.4)";
    ctx.lineWidth = 1;
    const gridStepPx = 1.0 * viewScale;

    // Center crosshair (0,0)
    ctx.strokeStyle = "rgba(108, 112, 134, 0.6)";
    ctx.beginPath();
    ctx.moveTo(-1000, 0);
    ctx.lineTo(1000, 0);
    ctx.moveTo(0, -1000);
    ctx.lineTo(0, 1000);
    ctx.stroke();

    // 4. Draw Trajectory Trail (Historical breadcrumbs)
    if (trajectory.length > 1) {
      ctx.strokeStyle = "#F9E2AF";
      ctx.lineWidth = Math.max(2, viewScale * 0.04);
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      for (let i = 0; i < trajectory.length; i++) {
        const [tx, ty] = trajectory[i];
        const px = tx * viewScale;
        const py = -ty * viewScale; // Invert Y
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.stroke();
    }

    // 5. Draw Laser Scan Rays (Instantaneous laser hits)
    if (laserScan.length > 0) {
      const rx = robotPose.x_m * viewScale;
      const ry = -robotPose.y_m * viewScale;

      ctx.fillStyle = "#F38BA8";
      for (let i = 0; i < laserScan.length; i++) {
        const [hx, hy] = laserScan[i];
        const px = hx * viewScale;
        const py = -hy * viewScale;

        // Draw hit point dot
        ctx.beginPath();
        ctx.arc(px, py, Math.max(2, viewScale * 0.03), 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // 6. Draw Robot Pose Avatar
    const rx = robotPose.x_m * viewScale;
    const ry = -robotPose.y_m * viewScale;
    const rRadius = Math.max(12, viewScale * 0.18); // ~18cm robot radius

    ctx.save();
    ctx.translate(rx, ry);
    // Rotate by heading: theta_deg (0° is North / -Y in canvas coords)
    ctx.rotate((robotPose.theta_deg * Math.PI) / 180.0);

    // Robot body circle
    ctx.fillStyle = "#89B4FA";
    ctx.beginPath();
    ctx.arc(0, 0, rRadius, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#FFF";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Direction pointer triangle (pointing forward UP in local frame)
    ctx.fillStyle = "#F5E0DC";
    ctx.beginPath();
    ctx.moveTo(0, -rRadius - 6);
    ctx.lineTo(-rRadius * 0.6, 0);
    ctx.lineTo(rRadius * 0.6, 0);
    ctx.closePath();
    ctx.fill();

    ctx.restore();

    ctx.restore();
  }

  // ------------------------------------------------------------------------
  // Pan & Zoom Gestures
  // ------------------------------------------------------------------------

  viewport.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const mouseX = e.clientX * window.devicePixelRatio;
    const mouseY = e.clientY * window.devicePixelRatio;

    // Zoom centered on cursor
    viewPanX = mouseX - (mouseX - viewPanX) * zoomFactor;
    viewPanY = mouseY - (mouseY - viewPanY) * zoomFactor;
    viewScale *= zoomFactor;
    viewScale = Math.max(5.0, Math.min(200.0, viewScale));

    render();
  }, { passive: false });

  viewport.addEventListener("mousedown", (e) => {
    if (e.target !== canvas) return;
    isDragging = true;
    dragStartX = e.clientX * window.devicePixelRatio - viewPanX;
    dragStartY = e.clientY * window.devicePixelRatio - viewPanY;
  });

  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    viewPanX = e.clientX * window.devicePixelRatio - dragStartX;
    viewPanY = e.clientY * window.devicePixelRatio - dragStartY;
    render();
  });

  window.addEventListener("mouseup", () => {
    isDragging = false;
  });

  // Touch Pan/Zoom
  let touchStartDist = 0;
  viewport.addEventListener("touchstart", (e) => {
    if (e.touches.length === 1 && e.target === canvas) {
      isDragging = true;
      dragStartX = e.touches[0].clientX * window.devicePixelRatio - viewPanX;
      dragStartY = e.touches[0].clientY * window.devicePixelRatio - viewPanY;
    } else if (e.touches.length === 2) {
      isDragging = false;
      touchStartDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  });

  viewport.addEventListener("touchmove", (e) => {
    if (isDragging && e.touches.length === 1) {
      viewPanX = e.touches[0].clientX * window.devicePixelRatio - dragStartX;
      viewPanY = e.touches[0].clientY * window.devicePixelRatio - dragStartY;
      render();
    } else if (e.touches.length === 2 && touchStartDist > 0) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const factor = dist / touchStartDist;
      viewScale = Math.max(5.0, Math.min(200.0, viewScale * factor));
      touchStartDist = dist;
      render();
    }
  });

  viewport.addEventListener("touchend", () => {
    isDragging = false;
    touchStartDist = 0;
  });

  // Zoom / Recenter Buttons
  btnZoomIn.addEventListener("click", () => {
    viewScale *= 1.25;
    render();
  });

  btnZoomOut.addEventListener("click", () => {
    viewScale *= 0.8;
    render();
  });

  btnRecenter.addEventListener("click", () => {
    viewPanX = canvas.width / 2 - robotPose.x_m * viewScale;
    viewPanY = canvas.height / 2 + robotPose.y_m * viewScale;
    render();
  });

  // Export Map as High-Res PNG
  btnExportPng.addEventListener("click", () => {
    const link = document.createElement("a");
    link.download = `${activeMapName.toLowerCase().replace(/\s+/g, "_")}_floorplan.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
  });

  // ------------------------------------------------------------------------
  // Virtual Touch Joystick & Driving Controls
  // ------------------------------------------------------------------------

  let isJoystickActive = false;
  let joystickCenter = { x: 0, y: 0 };
  let joystickMaxRadius = 40;
  let activeDriveCommand = null;
  let driveRepeatInterval = null;

  function sendDrive(action, continuous = false) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "drive",
        action: action,
        speed: currentSpeed,
        continuous: continuous,
      }));
    }
  }

  function sendStop() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }
  }

  function handleJoystickMove(clientX, clientY) {
    const dx = clientX - joystickCenter.x;
    const dy = clientY - joystickCenter.y;
    const dist = Math.hypot(dx, dy);
    const angle = Math.atan2(dy, dx); // rad

    const clampedDist = Math.min(dist, joystickMaxRadius);
    const knobX = clampedDist * Math.cos(angle);
    const knobY = clampedDist * Math.sin(angle);

    joystickKnob.style.transform = `translate(calc(-50% + ${knobX}px), calc(-50% + ${knobY}px))`;

    if (dist < 10) {
      if (activeDriveCommand) {
        activeDriveCommand = null;
        sendStop();
      }
      return;
    }

    // Determine 8-way directional command
    const deg = (angle * 180) / Math.PI; // -180 to 180
    let cmd = "forward";

    if (deg >= -67.5 && deg < -22.5) cmd = "forwardRight";
    else if (deg >= -112.5 && deg < -67.5) cmd = "forward";
    else if (deg >= -157.5 && deg < -112.5) cmd = "forwardLeft";
    else if (deg >= 22.5 && deg < 67.5) cmd = "backwardRight";
    else if (deg >= 67.5 && deg < 112.5) cmd = "backward";
    else if (deg >= 112.5 && deg < 157.5) cmd = "backwardLeft";
    else if (deg >= -22.5 && deg < 22.5) cmd = "strafeRight";
    else cmd = "strafeLeft";

    if (activeDriveCommand !== cmd) {
      activeDriveCommand = cmd;
      sendDrive(cmd, true);
    }
  }

  joystickContainer.addEventListener("pointerdown", (e) => {
    isJoystickActive = true;
    const rect = joystickContainer.getBoundingClientRect();
    joystickCenter = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    handleJoystickMove(e.clientX, e.clientY);
  });

  window.addEventListener("pointermove", (e) => {
    if (isJoystickActive) {
      handleJoystickMove(e.clientX, e.clientY);
    }
  });

  window.addEventListener("pointerup", () => {
    if (isJoystickActive) {
      isJoystickActive = false;
      activeDriveCommand = null;
      joystickKnob.style.transform = "translate(-50%, -50%)";
      sendStop();
    }
  });

  // Speed Slider
  speedSlider.addEventListener("input", (e) => {
    currentSpeed = parseInt(e.target.value, 10);
    lblSpeedVal.textContent = `Speed: ${currentSpeed}`;
  });

  // ------------------------------------------------------------------------
  // Keyboard Driving Controls (WASD / Arrows / QE)
  // ------------------------------------------------------------------------

  const activeKeys = new Set();

  window.addEventListener("keydown", (e) => {
    // Ignore when typing inside text inputs
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;

    const key = e.key.toLowerCase();
    if (activeKeys.has(key)) return;
    activeKeys.add(key);

    let cmd = null;
    if (key === "w" || key === "arrowup") cmd = "forward";
    else if (key === "s" || key === "arrowdown") cmd = "backward";
    else if (key === "a" || key === "arrowleft") cmd = "strafeLeft";
    else if (key === "d" || key === "arrowright") cmd = "strafeRight";
    else if (key === "q") cmd = "rotateLeft";
    else if (key === "e") cmd = "rotateRight";
    else if (key === " ") {
      sendStop();
      return;
    }

    if (cmd) {
      sendDrive(cmd, true);
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    const key = e.key.toLowerCase();
    activeKeys.delete(key);

    if (activeKeys.size === 0) {
      sendStop();
    }
  });

  // ------------------------------------------------------------------------
  // Mapping Controls & SQLite REST APIs
  // ------------------------------------------------------------------------

  btnToggleMapping.addEventListener("click", () => {
    if (isMapping) {
      fetch("/api/mapping/pause", { method: "POST" });
    } else {
      if (modalModeSelect) {
        modalModeSelect.classList.remove("hidden");
      } else {
        fetch("/api/mapping/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "autonomous" }),
        });
      }
    }
  });

  if (btnCloseModeModal) {
    btnCloseModeModal.addEventListener("click", () => {
      modalModeSelect.classList.add("hidden");
    });
  }

  if (btnStartAuto) {
    btnStartAuto.addEventListener("click", async () => {
      modalModeSelect.classList.add("hidden");
      try {
        await fetch("/api/mapping/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "autonomous" }),
        });
      } catch (e) {
        console.error("Failed to start autonomous mapping:", e);
      }
    });
  }

  if (btnStartManual) {
    btnStartManual.addEventListener("click", async () => {
      modalModeSelect.classList.add("hidden");
      try {
        await fetch("/api/mapping/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "manual" }),
        });
      } catch (e) {
        console.error("Failed to start manual mapping:", e);
      }
    });
  }


  btnResetMap.addEventListener("click", () => {
    if (confirm("Reset current occupancy grid map and robot pose?")) {
      fetch("/api/mapping/reset", { method: "POST" });
    }
  });

  // Save Map Modal
  btnSaveMap.addEventListener("click", () => {
    modalSave.classList.remove("hidden");
    inputMapName.focus();
  });

  btnCancelSave.addEventListener("click", () => {
    modalSave.classList.add("hidden");
  });

  btnConfirmSave.addEventListener("click", async () => {
    const name = inputMapName.value.trim() || "House Floorplan";
    try {
      const res = await fetch("/api/maps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      });
      if (res.ok) {
        modalSave.classList.add("hidden");
        alert(`Floorplan '${name}' saved successfully to SQLite!`);
      }
    } catch (err) {
      alert("Error saving map: " + err);
    }
  });

  // Map Library Modal
  btnOpenLibrary.addEventListener("click", async () => {
    modalLibrary.classList.remove("hidden");
    loadMapsList();
  });

  btnCloseLibrary.addEventListener("click", () => {
    modalLibrary.classList.add("hidden");
  });

  async function loadMapsList() {
    mapsListContainer.innerHTML = "<p>Loading saved floorplans...</p>";
    try {
      const res = await fetch("/api/maps");
      const maps = await res.json();

      if (!maps || maps.length === 0) {
        mapsListContainer.innerHTML = "<p>No saved house maps found in database.</p>";
        return;
      }

      mapsListContainer.innerHTML = "";
      maps.forEach((m) => {
        const row = document.createElement("div");
        row.className = "map-card-row";
        row.innerHTML = `
          <div class="map-card-info">
            <div class="map-card-title">${m.name} ${m.is_active ? "🟢 (Active)" : ""}</div>
            <div class="map-card-meta">${m.width}x${m.height} cells · ${m.resolution_cm} cm/px · ${new Date(m.updated_at).toLocaleString()}</div>
          </div>
          <div class="map-card-actions">
            <button class="btn btn-primary btn-sm btn-load-map" data-id="${m.id}">Load</button>
            <button class="btn btn-danger-outline btn-sm btn-del-map" data-id="${m.id}">Delete</button>
          </div>
        `;
        mapsListContainer.appendChild(row);
      });

      // Bind actions
      document.querySelectorAll(".btn-load-map").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          const mapId = e.target.getAttribute("data-id");
          await fetch(`/api/maps/${mapId}/load`, { method: "POST" });
          modalLibrary.classList.add("hidden");
        });
      });

      document.querySelectorAll(".btn-del-map").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          const mapId = e.target.getAttribute("data-id");
          if (confirm("Delete this house map from database?")) {
            await fetch(`/api/maps/${mapId}`, { method: "DELETE" });
            loadMapsList();
          }
        });
      });
    } catch (err) {
      mapsListContainer.innerHTML = `<p style="color:var(--red);">Error loading maps: ${err}</p>`;
    }
  }

  // ------------------------------------------------------------------------
  // Initialize
  // ------------------------------------------------------------------------
  resizeCanvas();
  connectWebSocket();
})();
