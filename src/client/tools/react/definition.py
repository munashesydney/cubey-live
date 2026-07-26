"""
Definition of the 'react' tool for Gemini Multimodal Live API.
Declares the FunctionDeclaration schema registered with Gemini Live.
"""

from google.genai import types

# Function declaration for the 'react' tool
REACT_FUNCTION_DECLARATION = types.FunctionDeclaration(
    name="react",
    description=(
        "Triggers a visual facial emotion reaction on the robot's screen display. "
        "Call this tool whenever you experience physical interactions (e.g. being kicked, moved), "
        "or whenever your emotional tone shifts."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reaction_type": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The visual reaction type to express on the display: "
                    "'hurt' (when kicked or damaged), 'alert' (when obstacle detected or moved), "
                    "'happy' (warm/friendly), 'low_battery' (power low), or 'normal'."
                ),
            )
        },
        required=["reaction_type"],
    ),
)

# Tool container for Gemini Live connection config
REACT_TOOL_DECLARATION = types.Tool(
    function_declarations=[REACT_FUNCTION_DECLARATION]
)
