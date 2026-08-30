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
  const ctx = canvas ? canvas.getContext("2d", { alpha: false }) : null;
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

  if (!canvas || !ctx || !viewport) {
    return;
  }

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
  // Coordinate Transforms
  // ------------------------------------------------------------------------

  function worldToCanvas(worldXM, worldYM) {
    const cx = viewPanX + (worldXM * viewScale);
    const cy = viewPanY - (worldYM * viewScale);
    return { x: cx, y: cy };
  }

  function canvasToWorld(canvasX, canvasY) {
    const wx = (canvasX - viewPanX) / viewScale;
    const wy = (viewPanY - canvasY) / viewScale;
    return { x: wx, y: wy };
  }

  // ------------------------------------------------------------------------
  // Canvas Pan & Zoom Handlers
  // ------------------------------------------------------------------------

  viewport.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const rect = canvas.getBoundingClientRect();
    const mouseCanvasX = (e.clientX - rect.left) * window.devicePixelRatio;
    const mouseCanvasY = (e.clientY - rect.top) * window.devicePixelRatio;

    const targetWorld = canvasToWorld(mouseCanvasX, mouseCanvasY);
    viewScale = Math.max(5.0, Math.min(150.0, viewScale * zoomFactor));

    viewPanX = mouseCanvasX - (targetWorld.x * viewScale);
    viewPanY = mouseCanvasY + (targetWorld.y * viewScale);
    render();
  }, { passive: false });

  viewport.addEventListener("mousedown", (e) => {
    if (e.target !== canvas) return;
    isDragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
  });

  window.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    const dx = (e.clientX - dragStartX) * window.devicePixelRatio;
    const dy = (e.clientY - dragStartY) * window.devicePixelRatio;
    dragStartX = e.clientX;
    dragStartY = e.clientY;

    viewPanX += dx;
    viewPanY += dy;
    render();
  });

  window.addEventListener("mouseup", () => {
    isDragging = false;
  });

  // Touch Pan & Pinch Zoom
  let touchDistStart = 0;
  viewport.addEventListener("touchstart", (e) => {
    if (e.target !== canvas) return;
    if (e.touches.length === 1) {
      isDragging = true;
      dragStartX = e.touches[0].clientX;
      dragStartY = e.touches[0].clientY;
    } else if (e.touches.length === 2) {
      touchDistStart = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }, { passive: true });

  viewport.addEventListener("touchmove", (e) => {
    if (e.target !== canvas) return;
    if (e.touches.length === 1 && isDragging) {
      const dx = (e.touches[0].clientX - dragStartX) * window.devicePixelRatio;
      const dy = (e.touches[0].clientY - dragStartY) * window.devicePixelRatio;
      dragStartX = e.touches[0].clientX;
      dragStartY = e.touches[0].clientY;
      viewPanX += dx;
      viewPanY += dy;
      render();
    } else if (e.touches.length === 2) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      if (touchDistStart > 0) {
        const factor = dist / touchDistStart;
        viewScale = Math.max(5.0, Math.min(150.0, viewScale * factor));
        touchDistStart = dist;
        render();
      }
    }
  }, { passive: true });

  viewport.addEventListener("touchend", () => {
    isDragging = false;
    touchDistStart = 0;
  });

  // Navigation Goal by Double-Click
  canvas.addEventListener("dblclick", async (e) => {
    const rect = canvas.getBoundingClientRect();
    const clickX = (e.clientX - rect.left) * window.devicePixelRatio;
    const clickY = (e.clientY - rect.top) * window.devicePixelRatio;
    const world = canvasToWorld(clickX, clickY);

    if (confirm(`Send Cubey to waypoint (${world.x.toFixed(2)}m, ${world.y.toFixed(2)}m)?`)) {
      if (ws && isConnected) {
        ws.send(JSON.stringify({ type: "nav_goal", x_m: world.x, y_m: world.y }));
      } else {
        await fetch("/api/navigation/goal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ x_m: world.x, y_m: world.y, theta_deg: 0.0 }),
        });
      }
    }
  });

  // ------------------------------------------------------------------------
  // Canvas Rendering Pipeline
  // ------------------------------------------------------------------------

  function render() {
    if (!ctx) return;

    ctx.fillStyle = "#11111B";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    drawGridLines();
    drawOccupancyMap();
    drawTrajectory();
    drawLaserScan();
    drawRobot();
  }

  function drawGridLines() {
    const meterStepPx = viewScale;
    if (meterStepPx < 8) return;

    ctx.strokeStyle = "rgba(49, 50, 68, 0.4)";
    ctx.lineWidth = 1;

    const startX = viewPanX % meterStepPx;
    for (let x = startX; x < canvas.width; x += meterStepPx) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }

    const startY = viewPanY % meterStepPx;
    for (let y = startY; y < canvas.height; y += meterStepPx) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(canvas.width, y);
      ctx.stroke();
    }

    // Origin Axes
    ctx.strokeStyle = "rgba(137, 180, 250, 0.5)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(viewPanX, 0);
    ctx.lineTo(viewPanX, canvas.height);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(0, viewPanY);
    ctx.lineTo(canvas.width, viewPanY);
    ctx.stroke();
  }

  function drawOccupancyMap() {
    if (!gridBuffer || gridBuffer.length === 0) return;

    const cellW = resolutionM * viewScale;
    const cellH = resolutionM * viewScale;

    for (let gy = 0; gy < gridHeight; gy += 2) {
      const wy = originYM + (gy * resolutionM);
      const cy = viewPanY - (wy * viewScale);

      if (cy < -cellH || cy > canvas.height + cellH) continue;

      for (let gx = 0; gx < gridWidth; gx += 2) {
        const val = gridBuffer[gy * gridWidth + gx];
        if (val === -1) continue;

        const wx = originXM + (gx * resolutionM);
        const cx = viewPanX + (wx * viewScale);

        if (cx < -cellW || cx > canvas.width + cellW) continue;

        if (val >= 65) {
          ctx.fillStyle = "#CDD6F4";
          ctx.fillRect(cx, cy, cellW * 2, cellH * 2);
        } else if (val >= 0 && val <= 35) {
          ctx.fillStyle = "rgba(30, 30, 46, 0.85)";
          ctx.fillRect(cx, cy, cellW * 2, cellH * 2);
        }
      }
    }
  }

  function drawTrajectory() {
    if (trajectory.length < 2) return;

    ctx.strokeStyle = "rgba(250, 179, 135, 0.8)";
    ctx.lineWidth = Math.max(2, viewScale * 0.05);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    ctx.beginPath();
    const p0 = worldToCanvas(trajectory[0].x, trajectory[0].y);
    ctx.moveTo(p0.x, p0.y);

    for (let i = 1; i < trajectory.length; i++) {
      const p = worldToCanvas(trajectory[i].x, trajectory[i].y);
      ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
  }

  function drawLaserScan() {
    if (!laserScan || laserScan.length === 0) return;

    ctx.fillStyle = "rgba(243, 139, 168, 0.85)";
    const ptRadius = Math.max(2.0, viewScale * 0.035);

    const rad = (robotPose.theta_deg * Math.PI) / 180.0;
    const cosR = Math.cos(rad);
    const sinR = Math.sin(rad);

    for (let i = 0; i < laserScan.length; i++) {
      const pt = laserScan[i];
      if (pt.dist_mm <= 150) continue;

      const dM = pt.dist_mm / 1000.0;
      const angleRad = (pt.angle_deg * Math.PI) / 180.0;

      const lx = dM * Math.sin(angleRad);
      const ly = dM * Math.cos(angleRad) - 0.035;

      const wx = robotPose.x_m + (lx * cosR - ly * sinR);
      const wy = robotPose.y_m + (lx * sinR + ly * cosR);

      const p = worldToCanvas(wx, wy);
      ctx.beginPath();
      ctx.arc(p.x, p.y, ptRadius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawRobot() {
    const cp = worldToCanvas(robotPose.x_m, robotPose.y_m);
    const rad = (robotPose.theta_deg * Math.PI) / 180.0;

    const sizeM = 0.22;
    const sizePx = sizeM * viewScale;

    ctx.save();
    ctx.translate(cp.x, cp.y);
    ctx.rotate(-rad);

    ctx.fillStyle = "rgba(137, 180, 250, 0.25)";
    ctx.strokeStyle = "#89B4FA";
    ctx.lineWidth = 2.5;

    ctx.beginPath();
    ctx.roundRect(-sizePx / 2, -sizePx / 2, sizePx, sizePx, 6);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#A6E3A1";
    ctx.strokeStyle = "#11111B";
    ctx.lineWidth = 1.5;

    ctx.beginPath();
    ctx.moveTo(0, -sizePx * 0.7);
    ctx.lineTo(-sizePx * 0.35, -sizePx * 0.2);
    ctx.lineTo(sizePx * 0.35, -sizePx * 0.2);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#F9E2AF";
    ctx.beginPath();
    ctx.arc(0, 0, 4, 0, Math.PI * 2);
    ctx.fill();

    ctx.restore();
  }

  // ------------------------------------------------------------------------
  // WebSocket Streaming Pipeline
  // ------------------------------------------------------------------------

  function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${location.host}/ws/live_map`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      isConnected = true;
      if (pillStatus) {
        pillStatus.querySelector(".dot")?.classList.add("live-dot");
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "telemetry") {
          handleTelemetry(msg);
        }
      } catch (err) {
        console.warn("WS Parse Error:", err);
      }
    };

    ws.onclose = () => {
      isConnected = false;
      if (pillStatus) {
        pillStatus.querySelector(".dot")?.classList.remove("live-dot");
      }
      setTimeout(connectWebSocket, 1500);
    };
  }

  function handleTelemetry(data) {
    if (data.pose) {
      robotPose = data.pose;
      if (lblPoseText) {
        lblPoseText.textContent = `X:${robotPose.x_m.toFixed(2)}m Y:${robotPose.y_m.toFixed(2)}m ${Math.round(robotPose.theta_deg)}°`;
      }

      if (
        trajectory.length === 0 ||
        Math.hypot(
          trajectory[trajectory.length - 1].x - robotPose.x_m,
          trajectory[trajectory.length - 1].y - robotPose.y_m
        ) > 0.05
      ) {
        trajectory.push({ x: robotPose.x_m, y: robotPose.y_m });
        if (trajectory.length > 2000) trajectory.shift();
      }
    }

    if (data.laser_scan) {
      laserScan = data.laser_scan;
    }

    if (data.mapping) {
      isMapping = data.mapping.is_mapping;
      if (lblMappingState) {
        lblMappingState.textContent = isMapping ? "Mapping Active" : "Mapping Idle";
      }
      if (btnMappingText) {
        btnMappingText.textContent = isMapping ? "Pause Mapping" : "Start Mapping";
      }
      if (btnToggleMapping) {
        btnToggleMapping.className = isMapping ? "btn btn-danger-outline" : "btn btn-primary";
      }
      if (lblActiveMapName && data.mapping.map_name) {
        lblActiveMapName.textContent = data.mapping.map_name;
      }
    }

    if (data.battery && lblBatteryPct) {
      lblBatteryPct.textContent = `${data.battery.percentage}% ${data.battery.is_charging ? "⚡" : ""}`;
    }

    if (data.lidar && lblLidarHz) {
      lblLidarHz.textContent = `${(data.lidar.scan_rate_hz || 0).toFixed(1)} Hz`;
    }

    if (data.grid_meta) {
      gridWidth = data.grid_meta.width;
      gridHeight = data.grid_meta.height;
      resolutionM = data.grid_meta.resolution_m;
      originXM = data.grid_meta.origin_x_m;
      originYM = data.grid_meta.origin_y_m;
    }

    if (data.grid_compressed_b64 && window.pako) {
      try {
        const rawBytes = Uint8Array.from(atob(data.grid_compressed_b64), (c) => c.charCodeAt(0));
        const decompressed = window.pako.inflate(rawBytes);
        gridBuffer = new Int8Array(decompressed.buffer);
      } catch (e) {
        console.warn("Grid decompress error:", e);
      }
    }

    render();
  }

  // ------------------------------------------------------------------------
  // UI Actions & Modals
  // ------------------------------------------------------------------------

  if (btnRecenter) {
    btnRecenter.addEventListener("click", () => {
      viewPanX = canvas.width / 2 - (robotPose.x_m * viewScale);
      viewPanY = canvas.height / 2 + (robotPose.y_m * viewScale);
      render();
    });
  }

  if (btnZoomIn) {
    btnZoomIn.addEventListener("click", () => {
      viewScale = Math.min(150.0, viewScale * 1.25);
      render();
    });
  }

  if (btnZoomOut) {
    btnZoomOut.addEventListener("click", () => {
      viewScale = Math.max(5.0, viewScale * 0.8);
      render();
    });
  }

  if (btnExportPng) {
    btnExportPng.addEventListener("click", () => {
      const link = document.createElement("a");
      link.download = `cubey-map-${Date.now()}.png`;
      link.href = canvas.toDataURL("image/png");
      link.click();
    });
  }

  if (btnToggleMapping) {
    btnToggleMapping.addEventListener("click", () => {
      if (isMapping) {
        if (ws && isConnected) {
          ws.send(JSON.stringify({ type: "pause_mapping" }));
        } else {
          fetch("/api/mapping/pause", { method: "POST" });
        }
      } else {
        modalModeSelect.classList.remove("hidden");
      }
    });
  }

  if (btnCloseModeModal) {
    btnCloseModeModal.addEventListener("click", () => {
      modalModeSelect.classList.add("hidden");
    });
  }

  if (btnStartAuto) {
    btnStartAuto.addEventListener("click", () => {
      modalModeSelect.classList.add("hidden");
      if (ws && isConnected) {
        ws.send(JSON.stringify({ type: "start_mapping", mode: "autonomous" }));
      } else {
        fetch("/api/mapping/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "autonomous" }),
        });
      }
    });
  }

  if (btnStartManual) {
    btnStartManual.addEventListener("click", () => {
      modalModeSelect.classList.add("hidden");
      if (ws && isConnected) {
        ws.send(JSON.stringify({ type: "start_mapping", mode: "manual" }));
      } else {
        fetch("/api/mapping/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: "manual" }),
        });
      }
    });
  }

  if (btnResetMap) {
    btnResetMap.addEventListener("click", () => {
      if (confirm("Reset map grid? This clears unexplored space and restarts SLAM tracking.")) {
        trajectory = [];
        if (ws && isConnected) {
          ws.send(JSON.stringify({ type: "reset_map" }));
        } else {
          fetch("/api/mapping/reset", { method: "POST" });
        }
      }
    });
  }

  if (btnSaveMap) {
    btnSaveMap.addEventListener("click", () => {
      modalSave.classList.remove("hidden");
    });
  }

  if (btnCancelSave) {
    btnCancelSave.addEventListener("click", () => {
      modalSave.classList.add("hidden");
    });
  }

  if (btnConfirmSave) {
    btnConfirmSave.addEventListener("click", async () => {
      const name = inputMapName.value.trim() || "My House Map";
      await fetch("/api/maps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      });
      modalSave.classList.add("hidden");
      alert(`Map "${name}" saved to database.`);
    });
  }

  if (btnOpenLibrary) {
    btnOpenLibrary.addEventListener("click", async () => {
      modalLibrary.classList.remove("hidden");
      loadMapsList();
    });
  }

  if (btnCloseLibrary) {
    btnCloseLibrary.addEventListener("click", () => {
      modalLibrary.classList.add("hidden");
    });
  }

  async function loadMapsList() {
    if (!mapsListContainer) return;
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
  // Remote Drive Controls (Touch Joystick & WASD)
  // ------------------------------------------------------------------------

  if (speedSlider) {
    speedSlider.addEventListener("input", (e) => {
      currentSpeed = parseInt(e.target.value, 10);
      if (lblSpeedVal) lblSpeedVal.textContent = `Speed: ${currentSpeed}`;
    });
  }

  let isJoystickActive = false;
  let joystickInterval = null;
  let activeAction = "stop";

  if (joystickContainer && joystickKnob) {
    function handleJoystickMove(clientX, clientY) {
      const rect = joystickContainer.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const maxRadius = rect.width / 2 - 10;

      let dx = clientX - centerX;
      let dy = clientY - centerY;
      const dist = Math.hypot(dx, dy);

      if (dist > maxRadius) {
        dx = (dx / dist) * maxRadius;
        dy = (dy / dist) * maxRadius;
      }

      joystickKnob.style.transform = `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`;

      if (dist < 15) {
        activeAction = "stop";
        return;
      }

      const angleDeg = (Math.atan2(-dy, dx) * 180.0) / Math.PI;

      if (angleDeg >= 67.5 && angleDeg < 112.5) activeAction = "forward";
      else if (angleDeg >= 112.5 && angleDeg < 157.5) activeAction = "forwardLeft";
      else if (angleDeg >= 22.5 && angleDeg < 67.5) activeAction = "forwardRight";
      else if (angleDeg >= -22.5 && angleDeg < 22.5) activeAction = "strafeRight";
      else if (angleDeg >= -67.5 && angleDeg < -22.5) activeAction = "backwardRight";
      else if (angleDeg >= -112.5 && angleDeg < -67.5) activeAction = "backward";
      else if (angleDeg >= -157.5 && angleDeg < -112.5) activeAction = "backwardLeft";
      else activeAction = "strafeLeft";
    }

    function resetJoystick() {
      isJoystickActive = false;
      activeAction = "stop";
      joystickKnob.style.transform = "translate(-50%, -50%)";
      clearInterval(joystickInterval);
      joystickInterval = null;
      sendDriveCommand("stop");
    }

    joystickContainer.addEventListener("pointerdown", (e) => {
      isJoystickActive = true;
      joystickContainer.setPointerCapture(e.pointerId);
      handleJoystickMove(e.clientX, e.clientY);

      if (!joystickInterval) {
        joystickInterval = setInterval(() => {
          if (activeAction !== "stop") {
            sendDriveCommand(activeAction);
          }
        }, 120);
      }
    });

    joystickContainer.addEventListener("pointermove", (e) => {
      if (!isJoystickActive) return;
      handleJoystickMove(e.clientX, e.clientY);
    });

    joystickContainer.addEventListener("pointerup", resetJoystick);
    joystickContainer.addEventListener("pointercancel", resetJoystick);
  }

  // Keyboard Drive Controls
  const keysPressed = new Set();
  window.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) return;
    const k = e.key.toLowerCase();
    if (["w", "a", "s", "d", "q", "e", " "].includes(k)) {
      e.preventDefault();
      if (!keysPressed.has(k)) {
        keysPressed.add(k);
        evaluateKeyboardDrive();
      }
    }
  });

  window.addEventListener("keyup", (e) => {
    const k = e.key.toLowerCase();
    if (keysPressed.has(k)) {
      keysPressed.delete(k);
      evaluateKeyboardDrive();
    }
  });

  function evaluateKeyboardDrive() {
    if (keysPressed.has(" ")) {
      sendDriveCommand("stop");
      return;
    }

    const w = keysPressed.has("w");
    const s = keysPressed.has("s");
    const a = keysPressed.has("a");
    const d = keysPressed.has("d");
    const q = keysPressed.has("q");
    const e = keysPressed.has("e");

    if (q) sendDriveCommand("rotateLeft");
    else if (e) sendDriveCommand("rotateRight");
    else if (w && a) sendDriveCommand("forwardLeft");
    else if (w && d) sendDriveCommand("forwardRight");
    else if (s && a) sendDriveCommand("backwardLeft");
    else if (s && d) sendDriveCommand("backwardRight");
    else if (w) sendDriveCommand("forward");
    else if (s) sendDriveCommand("backward");
    else if (a) sendDriveCommand("strafeLeft");
    else if (d) sendDriveCommand("strafeRight");
    else sendDriveCommand("stop");
  }

  function sendDriveCommand(action) {
    if (ws && isConnected) {
      ws.send(JSON.stringify({
        type: "drive",
        action: action,
        speed: currentSpeed,
        duration_ms: 180,
      }));
    } else {
      fetch("/api/control/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: action,
          speed: currentSpeed,
          duration_ms: 180,
        }),
      });
    }
  }

  // ------------------------------------------------------------------------
  // Initialize
  // ------------------------------------------------------------------------
  resizeCanvas();
  connectWebSocket();
})();
