"""
Dev console pages.

Each tool (Gemini Live, Local Chat, Memories, Tasks) is an embedded
CTkFrame page inside the DeveloperWindow shell, navigated via the sidebar.
"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .home_page import HomePage
    from .live_page import LivePage
    from .local_chat_page import LocalChatPage
    from .memory_page import MemoryPage
    from .tasks_page import TasksPage
    from .wheels_page import WheelsPage

__all__ = [
    "HomePage",
    "LivePage",
    "LocalChatPage",
    "MemoryPage",
    "TasksPage",
    "WheelsPage",
]

_PAGE_MODULES = {
    "HomePage": ".home_page",
    "LivePage": ".live_page",
    "LocalChatPage": ".local_chat_page",
    "MemoryPage": ".memory_page",
    "TasksPage": ".tasks_page",
    "WheelsPage": ".wheels_page",
}


def __getattr__(name: str):
    """Load visual pages only when the developer window requests them."""
    module_name = _PAGE_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name, __name__), name)
