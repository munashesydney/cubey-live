#include "webpage.h"

// ============================================================
// WEB PAGE
// ============================================================
const char webpage[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta
    name="viewport"
    content="width=device-width, initial-scale=1,
             maximum-scale=1, user-scalable=no"
  >
  <meta name="apple-mobile-web-app-capable" content="yes">
  <title>Cubey Controller</title>

  <style>
    * {
      box-sizing: border-box;
      user-select: none;
      -webkit-user-select: none;
      -webkit-touch-callout: none;
      touch-action: manipulation;
    }

    body {
      margin: 0;
      min-height: 100vh;
      background: #0e0e13;
      color: white;
      font-family: Arial, sans-serif;
      overflow: hidden;
    }

    .app {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 22px;
      padding: 18px;
    }

    .side {
      width: 190px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .title {
      text-align: center;
      font-size: 36px;
      font-weight: bold;
    }

    #status {
      min-height: 24px;
      text-align: center;
      color: #aaaab5;
      font-size: 17px;
    }

    .dpad {
      width: min(72vh, 500px);
      height: min(72vh, 500px);
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      grid-template-rows: repeat(3, 1fr);
      gap: 11px;
    }

    button {
      border: none;
      border-radius: 20px;
      background: #32323b;
      color: white;
      font-size: 34px;
      font-weight: bold;
      box-shadow: 0 5px 0 #18181e;
    }

    button:active,
    button.active {
      transform: translateY(4px);
      background: #595969;
      box-shadow: 0 1px 0 #18181e;
    }

    .diagonal {
      background: #25252d;
      font-size: 30px;
    }

    .stop {
      background: #d52b3e;
      box-shadow: 0 5px 0 #741721;
      font-size: 22px;
    }

    .rotate {
      height: 110px;
      background: #3a3a45;
      font-size: 19px;
    }

    .rotate-symbol {
      display: block;
      font-size: 42px;
      margin-bottom: 4px;
    }

    .speed-box {
      background: #1c1c23;
      border-radius: 18px;
      padding: 17px;
    }

    .speed-label {
      text-align: center;
      margin-bottom: 12px;
      font-size: 17px;
    }

    input[type="range"] {
      width: 100%;
      height: 35px;
    }

    .help {
      color: #8d8d98;
      font-size: 14px;
      text-align: center;
      line-height: 1.5;
    }

    @media (orientation: portrait) {
      body {
        overflow: auto;
      }

      .app {
        flex-direction: column;
      }

      .side {
        width: min(92vw, 500px);
      }

      .dpad {
        width: min(92vw, 500px);
        height: min(92vw, 500px);
      }

      .rotate {
        height: 80px;
      }
    }
  </style>
</head>

<body>
  <div class="app">
    <div class="side">
      <div class="title">Cubey</div>
      <div id="status">Stopped</div>

      <button class="movement rotate" data-command="rotateLeft">
        <span class="rotate-symbol">↺</span>
        Rotate left
      </button>

      <div class="speed-box">
        <div class="speed-label">
          Speed: <span id="speedValue">180</span>
        </div>

        <input
          id="speedSlider"
          type="range"
          min="70"
          max="255"
          value="180"
        >
      </div>
    </div>

    <div class="dpad">
      <button class="movement diagonal" data-command="forwardLeft">↖</button>
      <button class="movement" data-command="forward">↑</button>
      <button class="movement diagonal" data-command="forwardRight">↗</button>

      <button class="movement" data-command="strafeLeft">←</button>
      <button class="stop" id="stopButton">STOP</button>
      <button class="movement" data-command="strafeRight">→</button>

      <button class="movement diagonal" data-command="backwardLeft">↙</button>
      <button class="movement" data-command="backward">↓</button>
      <button class="movement diagonal" data-command="backwardRight">↘</button>
    </div>

    <div class="side">
      <button class="movement rotate" data-command="rotateRight">
        <span class="rotate-symbol">↻</span>
        Rotate right
      </button>

      <div class="help">
        Hold to move.<br>
        Release to stop.<br><br>
        ← and → strafe.<br>
        ↺ and ↻ rotate.
      </div>
    </div>
  </div>

  <script>
    let activeButton = null;
    let repeatTimer = null;

    const statusElement = document.getElementById("status");

    const labels = {
      forward: "Forward",
      backward: "Backward",
      strafeLeft: "Strafing left",
      strafeRight: "Strafing right",
      rotateLeft: "Rotating left",
      rotateRight: "Rotating right",
      forwardLeft: "Forward-left",
      forwardRight: "Forward-right",
      backwardLeft: "Backward-left",
      backwardRight: "Backward-right"
    };

    function sendCommand(command) {
      fetch("/command?name=" + encodeURIComponent(command), {
        method: "GET",
        cache: "no-store"
      }).catch(function(error) {
        console.log(error);
      });
    }

    function stopMovement(sendStop = true) {
      if (repeatTimer !== null) {
        clearInterval(repeatTimer);
        repeatTimer = null;
      }

      if (activeButton !== null) {
        activeButton.classList.remove("active");
        activeButton = null;
      }

      if (sendStop) {
        sendCommand("stop");
      }

      statusElement.innerText = "Stopped";
    }

    function startMovement(command, button, event) {
      event.preventDefault();
      stopMovement(false);

      activeButton = button;
      activeButton.classList.add("active");
      statusElement.innerText = labels[command] || command;

      try {
        button.setPointerCapture(event.pointerId);
      } catch (error) {
      }

      sendCommand(command);

      repeatTimer = setInterval(function() {
        sendCommand(command);
      }, 200);
    }

    document.querySelectorAll(".movement").forEach(function(button) {
      button.addEventListener("pointerdown", function(event) {
        startMovement(button.dataset.command, button, event);
      });

      button.addEventListener("pointerup", function() {
        stopMovement();
      });

      button.addEventListener("pointercancel", function() {
        stopMovement();
      });
    });

    document.getElementById("stopButton")
      .addEventListener("pointerdown", function(event) {
        event.preventDefault();
        stopMovement();
      });

    const speedSlider = document.getElementById("speedSlider");

    speedSlider.addEventListener("input", function() {
      document.getElementById("speedValue").innerText =
        speedSlider.value;

      fetch("/speed?value=" + speedSlider.value, {
        cache: "no-store"
      }).catch(function(error) {
        console.log(error);
      });
    });

    window.addEventListener("blur", function() {
      stopMovement();
    });

    document.addEventListener("visibilitychange", function() {
      if (document.hidden) {
        stopMovement();
      }
    });

    document.addEventListener("contextmenu", function(event) {
      event.preventDefault();
    });
  </script>
</body>
</html>
)rawliteral";
