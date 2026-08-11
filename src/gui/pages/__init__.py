"""
Dev console pages.

Each tool (Gemini Live, Local Chat, Memories, Tasks) is an embedded
CTkFrame page inside the DeveloperWindow shell, navigated via the sidebar.
"""

from .home_page import HomePage
from .live_page import LivePage
from .local_chat_page import LocalChatPage
from .memory_page import MemoryPage
from .tasks_page import TasksPage

__all__ = ["HomePage", "LivePage", "LocalChatPage", "MemoryPage", "TasksPage"]
