"""
Main entry point for Gemini Live Voice & Interruption Simulator.
"""

import sys
import logging
from src.controller import ApplicationController

# Configure application logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
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
