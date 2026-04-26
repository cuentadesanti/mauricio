from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel

MemoryKind = Literal["fact", "preference", "entity", "note"]


class Memory(BaseModel):
    id: str | None = None
    user_id: str
    kind: MemoryKind
    content: str
    metadata: dict = {}
    source_chat_id: str | None = None
    source_message_id: str | None = None
    created_at: datetime | None = None


class MemoryStore(Protocol):
    async def store(self, memory: Memory) -> str: ...

    async def retrieve(
        self,
        user_id: str,
        query: str,
        k: int = 5,
        kinds: list[MemoryKind] | None = None,
        min_score: float = 0.5,
    ) -> list[tuple[Memory, float]]: ...

    async def find_similar(
        self,
        user_id: str,
        kind: MemoryKind,
        embedding: list[float],
        threshold: float = 0.92,
    ) -> Memory | None: ...
