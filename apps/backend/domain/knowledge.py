from typing import Protocol

from pydantic import BaseModel


class Document(BaseModel):
    id: str | None = None
    user_id: str
    s3_key: str  # ruta canonical (relative path local o key real en S3)
    title: str | None = None
    content: str  # markdown completo
    content_hash: str
    metadata: dict = {}


class Chunk(BaseModel):
    doc_id: str
    chunk_index: int
    content: str
    score: float | None = None


class KnowledgeStore(Protocol):
    async def upsert_document(self, doc: Document, chunks: list[str]) -> str: ...
    async def search(
        self, user_id: str, query: str, k: int = 5, min_score: float = 0.5
    ) -> list[Chunk]: ...
    async def get_document(self, doc_id: str) -> Document | None: ...
    async def list_documents(self, user_id: str) -> list[Document]: ...
