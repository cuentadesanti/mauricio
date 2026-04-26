from ..core.config import settings
from .base import Tool
from .lamp import LampTool
from .memory_edit import MemoryEditTool
from .note_add import NoteAddTool
from .note_list import NoteListTool
from .note_read import NoteReadTool
from .time_now import TimeNowTool
from .web_search import WebSearchTool


def build_registry() -> dict[str, Tool]:
    tools: dict[str, Tool] = {
        "time_now": TimeNowTool(),
        "note_list": NoteListTool(),
        "note_read": NoteReadTool(),
        "note_add": NoteAddTool(),
        "memory_edit": MemoryEditTool(),
    }
    if settings.tavily_api_key:
        tools["web_search"] = WebSearchTool()
    if settings.kasa_username and settings.kasa_password:
        tools["lamp"] = LampTool()
    return tools


REGISTRY = build_registry()


def openai_tool_specs() -> list[dict]:
    """Formato OpenAI tools[] para mandar al LLM."""
    return [
        {"type": "function", "function": tool.spec.model_dump()}
        for tool in REGISTRY.values()
    ]
