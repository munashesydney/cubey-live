# Cubey Raspberry Pi Startup

One-shot bootstrap for running Cubey on a fresh Raspberry Pi.

## Files

| File | Purpose |
|---|---|
| `setup_pi.sh` | Installs everything: system packages, Python deps, `.env`, DB migrations, local models, boot autostart. Idempotent. |
| `run.sh` | Starts Cubey with the project virtualenv (manual start). |
| `cubey.service` | systemd unit template installed by `setup_pi.sh` for boot autostart. |

## Prerequisites

- Raspberry Pi 4 or 5 running **64-bit Raspberry Pi OS** (Bookworm or newer).
- The project folder is already on the Pi (copy it over with `scp`, a USB
  drive, or similar). Do **not** clone over your old history; a fresh copy
  is cleanest.
- Network access for the initial install + model downloads.
- A desktop session on the Pi (Cubey's GUI needs a display).

## Usage

```bash
# from the project folder on the Pi
bash scripts/startup/setup_pi.sh
```

The script will:

1. Install system packages (`python3-venv`, `python3-tk`, PortAudio/ALSA,
   Pillow build deps, `alsa-utils`, `ffmpeg`).
2. Create a virtualenv at `.venv` and `pip install -r requirements.txt`.
3. Create `.env` from `env.example` and prompt for `GEMINI_API_KEY`.
4. Run `alembic upgrade head` to create `data/cubey.db`.
5. Pre-download the embedding model (`fastembed`) and Local LLM (`Qwen 2B GGUF`).
6. Print the detected audio devices.
7. Install and start the `cubey.service` systemd unit (boot autostart).

### Options

- `bash scripts/startup/setup_pi.sh --no-autostart` — skip the systemd service.

### After setup

```bash
bash scripts/startup/run.sh          # manual start
```

The systemd service starts Cubey automatically at boot. Manage it with:

```bash
sudo systemctl status cubey          # check status
sudo systemctl restart cubey         # restart
sudo systemctl disable --now cubey   # stop auto-starting
journalctl -u cubey -n 50            # recent logs
```

## Troubleshooting

- **`command not found` / `$'\r'` errors when running the scripts** — the
  files were likely copied from Windows with CRLF line endings. Fix:
  `sed -i 's/\r$//' scripts/startup/setup_pi.sh scripts/startup/run.sh`
- **Wrong audio device** (e.g. HDMI instead of the headphone jack): run
  `sudo raspi-config` → System Options → Audio, or set it with
  `amixer cset numid=3 <n>` / `speaker-test`.
- **GUI won't start under systemd** — the service assumes the desktop session
  is on `DISPLAY=:0` and that your user has a logged-in session. If you use a
  different display number, edit `/etc/systemd/system/cubey.service`
  (`Environment=DISPLAY=:1`) and `sudo systemctl daemon-reload && sudo systemctl restart cubey`.
- **No GEMINI_API_KEY set** — the app will refuse to start a session. Edit
  `.env` in the project root.
- **Re-running setup** — safe. Existing `.env`, models, and database are
  preserved; migrations are no-ops when already current.
