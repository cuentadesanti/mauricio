from ..core.config import settings
from .base import Tool
from .note_add import NoteAddTool
from .time_now import TimeNowTool
from .web_search import WebSearchTool


def build_registry() -> dict[str, Tool]:
    tools: dict[str, Tool] = {
        "time_now": TimeNowTool(),
        "note_add": NoteAddTool(),
    }
    if settings.tavily_api_key:
        tools["web_search"] = WebSearchTool()
    return tools


REGISTRY = build_registry()


def openai_tool_specs() -> list[dict]:
    """Formato OpenAI tools[] para mandar al LLM."""
    return [
        {"type": "function", "function": tool.spec.model_dump()}
        for tool in REGISTRY.values()
    ]
