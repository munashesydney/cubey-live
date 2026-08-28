"""
Execution logic for the 'react' tool call.
Dispatches tool execution from Gemini Live directly to the visual reaction animation engine.
"""

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

VALID_REACTION_TYPES = {
    "hurt",
    "alert",
    "happy",
    "shocked",
    "surprised",
    "skeptical",
    "low_battery",
    "charging",
    "sleeping",
    "normal",
}

def execute_react_tool(
    reaction_type: str,
    on_trigger_reaction: Optional[Callable[[str], None]] = None
) -> dict[str, Any]:
    """
    Executes the 'react' tool call received from Gemini Live API.
    Invokes the reaction animation module and returns confirmation dict for send_tool_response.
    """
    clean_reaction = str(reaction_type).lower().strip()
    
    if clean_reaction not in VALID_REACTION_TYPES:
        logger.warning("Received unknown reaction_type: '%s', defaulting to 'hurt'", reaction_type)
        clean_reaction = "hurt"

    logger.info("⚡ AI Tool Call Executed: react(reaction_type='%s')", clean_reaction)

    # Dispatch to visual reaction animation engine via callback
    if on_trigger_reaction:
        on_trigger_reaction(clean_reaction)

    return {
        "status": "reaction_executed",
        "reaction_type": clean_reaction,
        "message": f"Successfully triggered visual '{clean_reaction}' reaction animation on screen."
    }
