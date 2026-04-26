from typing import Any, Protocol

from pydantic import BaseModel


class ToolSpec(BaseModel):
    """Lo que se le pasa al LLM en formato OpenAI tools."""

    name: str
    description: str
    parameters: dict  # JSON Schema


class Tool(Protocol):
    spec: ToolSpec

    async def run(self, args: dict, ctx: dict) -> Any:
        """Ejecuta la tool. ctx incluye user_id, chat_id, etc."""
        ...
