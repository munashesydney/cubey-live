"""Definition of the ``add_face`` tool."""

from src.client.tools.registry import build_gemini_tool

ADD_FACE_TOOL_DECLARATION = build_gemini_tool("add_face")
ADD_FACE_FUNCTION_DECLARATION = ADD_FACE_TOOL_DECLARATION.function_declarations[0]

