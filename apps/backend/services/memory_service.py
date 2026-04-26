from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.repository import Repository
from ..gateways.embeddings_gateway import EmbeddingsGateway


class MemoryService:
    def __init__(self, embeddings: EmbeddingsGateway):
        self.emb = embeddings

    async def store_unique(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        kind: str,
        content: str,
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
    ) -> str | None:
        """Guarda solo si no es duplicado. Returns id si guardada, None si duplicada."""
        repo = Repository(session)
        embedding = await self.emb.embed_one(content)

        existing = await repo.find_similar_memory(
            user_id=user_id,
            kind=kind,
            embedding=embedding,
            threshold=settings.memory_dedup_threshold,
        )
        if existing:
            return None

        m = await repo.insert_memory(
            user_id=user_id,
            kind=kind,
            content=content,
            embedding=embedding,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        return m.id

    async def retrieve_relevant(
        self, session: AsyncSession, user_id: str, query: str, k: int = 5
    ) -> list[tuple[str, str, float]]:
        """Returns list of (kind, content, score)."""
        repo = Repository(session)
        emb = await self.emb.embed_one(query)
        results = await repo.search_memories(user_id, emb, k=k)
        return [(m.kind, m.content, score) for m, score in results]
