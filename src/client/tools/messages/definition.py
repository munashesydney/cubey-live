"""
Definition of the 'messages' tool for Gemini Multimodal Live API.
Declares the FunctionDeclaration schema registered with Gemini Live.
"""

from google.genai import types

# Function declaration for the 'messages' tool
MESSAGES_FUNCTION_DECLARATION = types.FunctionDeclaration(
    name="messages",
    description=(
        "Search Cubey's memory of past conversation messages by semantic meaning. "
        "Call this tool when you need to recall something the user said before, "
        "anything discussed in an earlier conversation, or facts about the user's "
        "life. Returns the most similar past messages with their content, role, "
        "and when they happened."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description=(
                    "A natural-language description of what to look for, e.g. "
                    "'what did the user say about their dog' or 'when did we talk "
                    "about the kitchen project'."
                ),
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of matching messages to return (1-10).",
                default=5,
            ),
        },
        required=["query"],
    ),
)

# Tool container for Gemini Live connection config
MESSAGES_TOOL_DECLARATION = types.Tool(
    function_declarations=[MESSAGES_FUNCTION_DECLARATION]
)
