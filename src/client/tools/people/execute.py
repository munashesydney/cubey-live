"""Execution logic for saving the currently pending face enrollment."""

from typing import Any, Optional


def execute_add_face_tool(
    name: str,
    face_recognition_service: Optional[Any] = None,
) -> dict[str, Any]:
    """Save the in-memory enrollment under the name supplied by the user."""
    display_name = " ".join(str(name or "").strip().split())
    if not display_name:
        return {
            "status": "validation_error",
            "message": "A non-empty person name is required to save the face.",
        }
    if face_recognition_service is None:
        return {
            "status": "unavailable",
            "message": "Face recognition is not available right now.",
        }

    try:
        saved, message = face_recognition_service.save_pending_enrollment(display_name)
    except Exception as exc:
        return {
            "status": "tool_error",
            "message": f"Could not save the face: {exc}",
        }

    if not saved:
        return {
            "status": "save_failed",
            "name": display_name,
            "message": message,
        }
    return {
        "status": "face_saved",
        "name": display_name,
        "message": message,
    }

