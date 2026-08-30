/**
 * Cubey Portal — Microphone Diagnostics & Denoiser Studio Script
 */

(function () {
  "use strict";

  const chkToggleDenoiser = document.getElementById("chk-toggle-denoiser");
  const lblAudioDeviceSpec = document.getElementById("lbl-audio-device-spec");

  const lblRawDb = document.getElementById("lbl-raw-db");
  const barRawFill = document.getElementById("bar-raw-fill");
  const indRawPeak = document.getElementById("ind-raw-peak");
  const lblRawRmsPct = document.getElementById("lbl-raw-rms-pct");

  const lblDenoisedDb = document.getElementById("lbl-denoised-db");
  const barDenoisedFill = document.getElementById("bar-denoised-fill");
  const indDenoisedPeak = document.getElementById("ind-denoised-peak");
  const lblNoiseReductionDb = document.getElementById("lbl-noise-reduction-db");
  const badgeVad = document.getElementById("badge-vad");

  const scopeCanvas = document.getElementById("scope-canvas");
  const scopeCtx = scopeCanvas ? scopeCanvas.getContext("2d") : null;

  const btnToggleLiveMonitor = document.getElementById("btn-toggle-live-monitor");
  const lblMonitorIcon = document.getElementById("lbl-monitor-icon");
  const lblMonitorText = document.getElementById("lbl-monitor-text");

  const btnStartRecordTest = document.getElementById("btn-start-record-test");
  const lblRecordTestText = document.getElementById("lbl-record-test-text");
  const recordProgressBarWrap = document.getElementById("record-progress-bar-wrap");
  const recordProgressBar = document.getElementById("record-progress-bar");
  const testPlaybackRow = document.getElementById("test-playback-row");
  const btnPlayDenoisedClip = document.getElementById("btn-play-denoised-clip");
  const btnPlayRawClip = document.getElementById("btn-play-raw-clip");
  const testAudioPlayer = document.getElementById("test-audio-player");

  let audioWs = null;
  let isLiveMonitorActive = false;
  let audioContext = null;
  let nextAudioTime = 0;

  let rawWaveData = new Array(32).fill(0);
  let denWaveData = new Array(32).fill(0);
  let scopeAnimId = null;

  let rawPeakDb = -60.0;
  let denPeakDb = -60.0;

  function connectAudioWs() {
    if (audioWs && (audioWs.readyState === WebSocket.OPEN || audioWs.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/audio_test`;
    audioWs = new WebSocket(url);

    audioWs.onopen = () => {
      console.log("🎙️ Audio studio WebSocket connected.");
    };

    audioWs.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleAudioTelemetry(msg);
      } catch (err) {
        console.warn("Audio WS parse error:", err);
      }
    };

    audioWs.onclose = () => {
      setTimeout(connectAudioWs, 1500);
    };
  }

  function handleAudioTelemetry(msg) {
    if (lblAudioDeviceSpec && msg.device_name) {
      lblAudioDeviceSpec.textContent = `${msg.device_name} · ${msg.sample_rate || 16000}Hz ${msg.channels === 1 ? 'Mono' : 'Stereo'}`;
    }

    if (chkToggleDenoiser && document.activeElement !== chkToggleDenoiser) {
      chkToggleDenoiser.checked = !!msg.is_denoiser_enabled;
    }

    // Raw Meter
    const rawDb = typeof msg.raw_db === "number" ? msg.raw_db : -60.0;
    const rawPct = Math.max(0, Math.min(100, (rawDb + 60.0) / 60.0 * 100.0));
    if (lblRawDb) lblRawDb.textContent = `${rawDb.toFixed(1)} dB`;
    if (barRawFill) barRawFill.style.width = `${rawPct}%`;
    if (lblRawRmsPct) lblRawRmsPct.textContent = `${Math.round(msg.raw_pct || 0)}%`;

    if (rawDb > rawPeakDb) {
      rawPeakDb = rawDb;
    } else {
      rawPeakDb = Math.max(-60, rawPeakDb - 0.5);
    }
    if (indRawPeak) {
      indRawPeak.style.left = `${Math.max(0, Math.min(99, (rawPeakDb + 60.0) / 60.0 * 100.0))}%`;
    }

    // Denoised Meter
    const denDb = typeof msg.denoised_db === "number" ? msg.denoised_db : -60.0;
    const denPct = Math.max(0, Math.min(100, (denDb + 60.0) / 60.0 * 100.0));
    if (lblDenoisedDb) lblDenoisedDb.textContent = `${denDb.toFixed(1)} dB`;
    if (barDenoisedFill) barDenoisedFill.style.width = `${denPct}%`;
    if (lblNoiseReductionDb) {
      lblNoiseReductionDb.textContent = `-${Math.max(0, msg.noise_reduction_db || 0).toFixed(1)} dB`;
    }

    if (denDb > denPeakDb) {
      denPeakDb = denDb;
    } else {
      denPeakDb = Math.max(-60, denPeakDb - 0.5);
    }
    if (indDenoisedPeak) {
      indDenoisedPeak.style.left = `${Math.max(0, Math.min(99, (denPeakDb + 60.0) / 60.0 * 100.0))}%`;
    }

    // VAD Pill
    if (badgeVad) {
      const vad = msg.vad_prob || 0.0;
      if (vad > 0.45) {
        badgeVad.textContent = `🗣️ Voice Active (${Math.round(vad * 100)}%)`;
        badgeVad.className = "badge-vad-status active-speech";
        badgeVad.style.background = "rgba(166, 227, 161, 0.3)";
        badgeVad.style.color = "#A6E3A1";
      } else {
        badgeVad.textContent = "🤫 Ambient Room";
        badgeVad.className = "badge-vad-status";
        badgeVad.style.background = "rgba(108, 112, 134, 0.2)";
        badgeVad.style.color = "#BAC2DE";
      }
    }

    // Waveform Oscilloscope Data
    if (Array.isArray(msg.waveform_raw) && msg.waveform_raw.length > 0) {
      rawWaveData = msg.waveform_raw;
    }
    if (Array.isArray(msg.waveform_denoised) && msg.waveform_denoised.length > 0) {
      denWaveData = msg.waveform_denoised;
    }

    // Live Audio Playback in Browser Speakers
    if (isLiveMonitorActive) {
      const srcRadio = document.querySelector('input[name="monitor-source"]:checked');
      const srcType = srcRadio ? srcRadio.value : "denoised";
      const b64Audio = srcType === "raw" ? msg.audio_raw_b64 : msg.audio_denoised_b64;
      if (b64Audio) {
        playLivePcmChunk(b64Audio, msg.sample_rate || 16000);
      }
    }

    // 5-Second Test Recording
    if (msg.is_recording_test) {
      if (recordProgressBarWrap) recordProgressBarWrap.classList.remove("hidden");
      if (recordProgressBar) recordProgressBar.style.width = `${msg.test_record_progress_pct || 0}%`;
      if (lblRecordTestText) lblRecordTestText.textContent = `Recording... (${Math.round(msg.test_record_progress_pct || 0)}%)`;
      if (testPlaybackRow) testPlaybackRow.classList.add("hidden");
    } else if (msg.test_record_progress_pct >= 99.0 || (recordProgressBarWrap && !recordProgressBarWrap.classList.contains("hidden"))) {
      if (recordProgressBarWrap) recordProgressBarWrap.classList.add("hidden");
      if (lblRecordTestText) lblRecordTestText.textContent = "Record 5s Test Clip";
      if (testPlaybackRow) testPlaybackRow.classList.remove("hidden");
    }
  }

  // Denoiser Master Switch Handler
  if (chkToggleDenoiser) {
    chkToggleDenoiser.addEventListener("change", async () => {
      const enabled = chkToggleDenoiser.checked;
      if (audioWs && audioWs.readyState === WebSocket.OPEN) {
        audioWs.send(JSON.stringify({ type: "toggle_denoiser", enabled: enabled }));
      } else {
        await fetch("/api/audio/denoiser/toggle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: enabled }),
        });
      }
    });
  }

  // Oscilloscope Animation Loop
  function startScopeAnimation() {
    if (!scopeCtx || !scopeCanvas) return;

    function drawScope() {
      const w = scopeCanvas.width;
      const h = scopeCanvas.height;
      const mid = h / 2;

      scopeCtx.fillStyle = "#11111B";
      scopeCtx.fillRect(0, 0, w, h);

      scopeCtx.strokeStyle = "rgba(255, 255, 255, 0.08)";
      scopeCtx.lineWidth = 1;
      scopeCtx.beginPath();
      scopeCtx.moveTo(0, mid);
      scopeCtx.lineTo(w, mid);
      scopeCtx.stroke();

      // Raw Trace
      if (rawWaveData && rawWaveData.length > 0) {
        scopeCtx.strokeStyle = "rgba(250, 179, 135, 0.75)";
        scopeCtx.lineWidth = 1.5;
        scopeCtx.beginPath();
        const step = w / (rawWaveData.length - 1);
        for (let i = 0; i < rawWaveData.length; i++) {
          const x = i * step;
          const y = mid - (rawWaveData[i] * (h * 0.42));
          if (i === 0) scopeCtx.moveTo(x, y);
          else scopeCtx.lineTo(x, y);
        }
        scopeCtx.stroke();
      }

      // Denoised Trace
      if (denWaveData && denWaveData.length > 0) {
        scopeCtx.strokeStyle = "#A6E3A1";
        scopeCtx.lineWidth = 2.0;
        scopeCtx.beginPath();
        const step = w / (denWaveData.length - 1);
        for (let i = 0; i < denWaveData.length; i++) {
          const x = i * step;
          const y = mid - (denWaveData[i] * (h * 0.42));
          if (i === 0) scopeCtx.moveTo(x, y);
          else scopeCtx.lineTo(x, y);
        }
        scopeCtx.stroke();
      }

      scopeAnimId = requestAnimationFrame(drawScope);
    }

    drawScope();
  }

  // Live Audio Monitor (Web Audio API)
  if (btnToggleLiveMonitor) {
    btnToggleLiveMonitor.addEventListener("click", () => {
      if (isLiveMonitorActive) {
        stopLiveMonitor();
      } else {
        startLiveMonitor();
      }
    });
  }

  function startLiveMonitor() {
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!audioContext || audioContext.state === "closed") {
        audioContext = new AudioContextClass({ sampleRate: 16000 });
      }
      if (audioContext.state === "suspended") {
        audioContext.resume();
      }
      isLiveMonitorActive = true;
      nextAudioTime = audioContext.currentTime + 0.05;
      if (lblMonitorIcon) lblMonitorIcon.textContent = "⏹️";
      if (lblMonitorText) lblMonitorText.textContent = "Stop Live Monitor";
      if (btnToggleLiveMonitor) btnToggleLiveMonitor.classList.add("btn-danger-outline");
    } catch (e) {
      alert("Browser audio output initialization failed: " + e);
    }
  }

  function stopLiveMonitor() {
    isLiveMonitorActive = false;
    if (lblMonitorIcon) lblMonitorIcon.textContent = "▶";
    if (lblMonitorText) lblMonitorText.textContent = "Start Live Audio Monitor";
    if (btnToggleLiveMonitor) btnToggleLiveMonitor.classList.remove("btn-danger-outline");
    if (audioContext && audioContext.state !== "closed") {
      try {
        audioContext.close();
      } catch (_) {}
      audioContext = null;
    }
  }

  function playLivePcmChunk(b64Data, sampleRate) {
    if (!audioContext || audioContext.state !== "running") return;

    try {
      const binaryString = window.atob(b64Data);
      const len = binaryString.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      const int16Array = new Int16Array(bytes.buffer);
      if (int16Array.length === 0) return;

      const audioBuffer = audioContext.createBuffer(1, int16Array.length, sampleRate);
      const channelData = audioBuffer.getChannelData(0);
      for (let i = 0; i < int16Array.length; i++) {
        channelData[i] = int16Array[i] / 32768.0;
      }

      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContext.destination);

      const currentTime = audioContext.currentTime;
      if (nextAudioTime < currentTime) {
        nextAudioTime = currentTime + 0.01;
      }
      source.start(nextAudioTime);
      nextAudioTime += audioBuffer.duration;
    } catch (err) {
      console.debug("Live PCM audio playback error:", err);
    }
  }

  // 5-Second Test Recording Trigger
  if (btnStartRecordTest) {
    btnStartRecordTest.addEventListener("click", async () => {
      if (testPlaybackRow) testPlaybackRow.classList.add("hidden");
      if (recordProgressBarWrap) recordProgressBarWrap.classList.remove("hidden");
      if (recordProgressBar) recordProgressBar.style.width = "0%";

      if (audioWs && audioWs.readyState === WebSocket.OPEN) {
        audioWs.send(JSON.stringify({ type: "start_record_test", duration_s: 5.0 }));
      } else {
        await fetch("/api/audio/test_recording/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ duration_s: 5.0 }),
        });
      }
    });
  }

  if (btnPlayDenoisedClip) {
    btnPlayDenoisedClip.addEventListener("click", () => {
      if (testAudioPlayer) {
        testAudioPlayer.src = `/api/audio/test_recording/denoised?t=${Date.now()}`;
        testAudioPlayer.play();
      }
    });
  }

  if (btnPlayRawClip) {
    btnPlayRawClip.addEventListener("click", () => {
      if (testAudioPlayer) {
        testAudioPlayer.src = `/api/audio/test_recording/raw?t=${Date.now()}`;
        testAudioPlayer.play();
      }
    });
  }

  // Initialize
  connectAudioWs();
  startScopeAnimation();
})();
