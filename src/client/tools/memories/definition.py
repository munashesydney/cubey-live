"""
Definition of the 'memories' tool for Gemini Multimodal Live API.
Declares the FunctionDeclaration schema registered with Gemini Live.
"""

from google.genai import types

# Function declaration for the 'memories' tool
MEMORIES_FUNCTION_DECLARATION = types.FunctionDeclaration(
    name="memories",
    description=(
        "Manage Cubey's long-term memory of durable facts about the user. "
        "Use 'add' to store a new fact the user shares that is worth "
        "remembering across sessions (preferences, names, life details). "
        "Use 'update' to correct or refine a stored memory by id. "
        "Use 'search' to recall stored memories relevant to the current "
        "conversation. Memories persist forever and are distinct from message "
        "history."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                enum=["add", "update", "search"],
                description="What to do with memory.",
            ),
            "content": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The memory text itself, phrased as a fact, e.g. "
                    "'The user's dog is named Biscuit'. Required for 'add'."
                ),
            ),
            "category": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Optional category: 'fact', 'preference', 'relationship', "
                    "'event', or 'task'."
                ),
            ),
            "importance": types.Schema(
                type=types.Type.INTEGER,
                description="How important the memory is, 1 (trivial) to 10 (critical).",
                default=5,
            ),
            "memory_id": types.Schema(
                type=types.Type.INTEGER,
                description="Id of the memory to update. Required for 'update'.",
            ),
            "query": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Natural-language description of what to recall. Required "
                    "for 'search'."
                ),
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description="Maximum number of memories to return (1-10).",
                default=5,
            ),
        },
        required=["action"],
    ),
)

# Tool container for Gemini Live connection config
MEMORIES_TOOL_DECLARATION = types.Tool(
    function_declarations=[MEMORIES_FUNCTION_DECLARATION]
)
