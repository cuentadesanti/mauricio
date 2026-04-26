from collections.abc import AsyncIterator
from typing import Protocol

from pydantic import BaseModel


class CompletionRequest(BaseModel):
    messages: list[dict]
    model_hint: str | None = None
    tools: list[dict] | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    metadata: dict = {}


class CompletionResponse(BaseModel):
    content: str
    tool_calls: list[dict] = []
    model_used: str
    usage: dict
    trace_id: str


class ModelGateway(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...
    def stream(self, req: CompletionRequest) -> AsyncIterator[str]: ...
