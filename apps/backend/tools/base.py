from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolSpec(BaseModel):
    """Lo que se le pasa al LLM en formato OpenAI tools."""

    name: str
    description: str
    parameters: dict  # JSON Schema


class Tool(ABC):
    """Base for every tool the LLM can call. Subclass, define `spec`, override `run`.

    Was a Protocol — switched to ABC so subclasses inherit the default
    `contexts` attribute without each having to redeclare it, and so mypy
    actually validates structure at definition time, not at call time.
    """

    spec: ToolSpec
    # TD-6: which channels this tool is available in.
    # ('any',) = all channels; ('voice',) = only voice; ('web', 'voice') = both.
    contexts: tuple[str, ...] = ("any",)

    # When True, the executor must NOT run this tool directly — instead it
    # surfaces a "pending confirmation" result so the user can approve/deny
    # via WhatsApp before the action takes effect. Today no tool sets this;
    # the scaffolding is here so when the first outbound/destructive tool
    # lands (whatsapp_send, file_delete, transfer_money…) it ships with the
    # gate already wired through the agentic loop. See chat_service tool
    # execution loop for the half-implementation.
    requires_confirmation: bool = False

    @abstractmethod
    async def run(self, args: dict, ctx: dict) -> Any:
        """Ejecuta la tool. ctx incluye user_id, chat_id, satellite_id, etc."""
        ...
