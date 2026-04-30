from ..core.config import settings
from .base import Tool
from .chat_search import ChatSearchTool
from .end_voice_chat import EndVoiceChatTool
from .lamp import LampTool
from .memory_edit import MemoryEditTool
from .memory_list import MemoryListTool
from .note_add import NoteAddTool
from .note_list import NoteListTool
from .note_read import NoteReadTool
from .propose_new_tool import ProposeNewToolTool
from .start_voice_chat import StartVoiceChatTool
from .time_now import TimeNowTool
from .web_search import WebSearchTool


def build_registry() -> dict[str, Tool]:
    tools: dict[str, Tool] = {
        "time_now": TimeNowTool(),
        "note_list": NoteListTool(),
        "note_read": NoteReadTool(),
        "note_add": NoteAddTool(),
        "memory_edit": MemoryEditTool(),
        "memory_list": MemoryListTool(),
        "chat_search": ChatSearchTool(),
        "start_voice_chat": StartVoiceChatTool(),
        "end_voice_chat": EndVoiceChatTool(),
    }
    if settings.tavily_api_key:
        tools["web_search"] = WebSearchTool()
    if settings.kasa_username and settings.kasa_password and settings.lamp_host:
        tools["lamp"] = LampTool()
    if settings.repo_root and settings.github_repo:
        # propose_new_tool requires both a checked-out repo to write to and a
        # GitHub remote to push branches/PRs to. Without these the tool would
        # error confusingly when the LLM calls it, so register it conditionally.
        tools["propose_new_tool"] = ProposeNewToolTool()
    return tools


REGISTRY = build_registry()


def _tool_matches_channel(tool, channel: str) -> bool:
    contexts = getattr(tool, "contexts", ("any",))
    return "any" in contexts or channel in contexts


def openai_tool_specs(channel: str = "any") -> list[dict]:
    """Formato OpenAI tools[] para mandar al LLM, filtrado por canal."""
    return [
        {"type": "function", "function": tool.spec.model_dump()}
        for tool in REGISTRY.values()
        if _tool_matches_channel(tool, channel)
    ]
