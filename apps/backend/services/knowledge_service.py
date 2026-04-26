import hashlib
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.repository import Repository
from ..gateways.embeddings_gateway import EmbeddingsGateway


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Chunker simple por caracteres con solape. Suficiente para markdowns."""
    if len(text) <= size:
        return [text.strip()] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # intentar cortar en un newline cercano
        if end < len(text):
            window = text.rfind("\n", start, end)
            if window > start + size // 2:
                end = window
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """YAML frontmatter muy simple. Soporta el formato que usa note_add."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_block = content[4:end]
    body = content[end + 5 :]
    meta = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


class KnowledgeService:
    def __init__(self, embeddings: EmbeddingsGateway):
        self.emb = embeddings

    async def sync_directory(self, session: AsyncSession, user_id: str) -> dict:
        """Lee knowledge/, indexa los cambios. Returns stats."""
        repo = Repository(session)
        root = Path(settings.knowledge_dir)
        root.mkdir(parents=True, exist_ok=True)

        stats = {"scanned": 0, "indexed": 0, "skipped_unchanged": 0, "errors": 0}

        for path in root.rglob("*.md"):
            stats["scanned"] += 1
            try:
                rel = str(path.relative_to(root))
                raw = path.read_text(encoding="utf-8")
                content_hash = hashlib.sha256(raw.encode()).hexdigest()
                meta, body = parse_frontmatter(raw)
                title = meta.get("title") or path.stem

                doc, changed = await repo.upsert_knowledge_doc(
                    user_id=user_id,
                    s3_key=rel,
                    title=title,
                    content_hash=content_hash,
                    metadata=meta,
                )
                if not changed:
                    stats["skipped_unchanged"] += 1
                    continue

                chunks = chunk_text(body, settings.chunk_size_chars, settings.chunk_overlap_chars)
                if not chunks:
                    stats["indexed"] += 1
                    continue

                embeddings = await self.emb.embed(chunks)
                rows = [(i, c, e) for i, (c, e) in enumerate(zip(chunks, embeddings))]
                await repo.insert_chunks(doc.id, rows)
                stats["indexed"] += 1
            except Exception as e:
                stats["errors"] += 1
                print(f"[knowledge sync] error on {path}: {e}")

        await session.commit()
        return stats

    async def search(
        self, session: AsyncSession, user_id: str, query: str, k: int = 5
    ) -> list[tuple[str, str, str, float]]:
        """Returns list of (title, content, doc_id, score)."""
        repo = Repository(session)
        emb = await self.emb.embed_one(query)
        results = await repo.search_chunks(user_id, emb, k=k)
        return [(title, c.content, c.doc_id, score) for c, title, score in results]
