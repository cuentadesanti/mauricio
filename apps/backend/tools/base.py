from typing import Any, Protocol

from pydantic import BaseModel


class ToolSpec(BaseModel):
    """Lo que se le pasa al LLM en formato OpenAI tools."""

    name: str
    description: str
    parameters: dict  # JSON Schema


class Tool(Protocol):
    spec: ToolSpec
    # TD-6: which channels this tool is available in.
    # ('any',) = all channels; ('voice',) = only voice; ('web', 'voice') = both.
    contexts: tuple[str, ...] = ("any",)

    async def run(self, args: dict, ctx: dict) -> Any:
        """Ejecuta la tool. ctx incluye user_id, chat_id, satellite_id, etc."""
        ...
