"""
Main entry point for Gemini Live Voice & Interruption Simulator.
"""

import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from src.controller import ApplicationController

# Keep a bounded incident log on the robot; stdout remains available to
# systemd/journald.  Navigation faults include the exact health/collision gate.
log_dir = Path(__file__).resolve().parent / "data" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(
    log_dir / "cubey.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), file_handler],
)
logger = logging.getLogger(__name__)

def main():
    try:
        controller = ApplicationController()
        controller.start()
    except KeyboardInterrupt:
        logger.info("Application exited by user.")
    except Exception as e:
        logger.critical("Fatal application error: %s", e, exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
