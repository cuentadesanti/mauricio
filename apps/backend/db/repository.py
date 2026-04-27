import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import delete, desc, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Chat,
    ChatSummary,
    Event,
    KnowledgeChunk,
    KnowledgeDoc,
    MemoryRow,
    Message,
    Satellite,
    User,
)


def hash_messages(messages: list[dict]) -> str:
    """Firma estable de un prefix de conversación."""
    canon = json.dumps(
        [{"role": m["role"], "content": m.get("content", "")} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canon.encode()).hexdigest()


class Repository:
    def __init__(self, session: AsyncSession):
        self.s = session

    # ---- users ----
    async def get_or_create_user(self, handle: str) -> User:
        res = await self.s.execute(select(User).where(User.handle == handle))
        user = res.scalar_one_or_none()
        if user:
            return user
        user = User(handle=handle)
        self.s.add(user)
        await self.s.flush()
        return user

    # ---- chats ----
    async def find_chat_by_signature(self, user_id: str, signature: str) -> Chat | None:
        res = await self.s.execute(
            select(Chat)
            .where(Chat.user_id == user_id, Chat.signature == signature)
            .order_by(desc(Chat.updated_at))
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def create_chat(
        self, user_id: str, channel: str, mode: str, signature: str | None = None
    ) -> Chat:
        chat = Chat(user_id=user_id, channel=channel, mode=mode, signature=signature)
        self.s.add(chat)
        await self.s.flush()
        return chat

    async def update_chat_signature(self, chat: Chat, signature: str) -> None:
        chat.signature = signature
        await self.s.flush()

    # ---- messages ----
    async def add_message(
        self,
        chat_id: str,
        role: str,
        content: dict,
        model: str | None = None,
        token_usage: dict | None = None,
        trace_id: str | None = None,
    ) -> Message:
        msg = Message(
            chat_id=chat_id,
            role=role,
            content=content,
            model=model,
            token_usage=token_usage,
            trace_id=trace_id,
        )
        self.s.add(msg)
        await self.s.flush()
        return msg

    async def get_messages(self, chat_id: str, limit: int = 30) -> list[Message]:
        res = await self.s.execute(
            select(Message)
            .where(Message.chat_id == chat_id)
            .order_by(Message.created_at)
            .limit(limit)
        )
        return list(res.scalars().all())

    async def count_messages(self, chat_id: str) -> int:
        res = await self.s.execute(
            text("SELECT count(*) FROM messages WHERE chat_id = :cid"), {"cid": chat_id}
        )
        return res.scalar_one()

    # ---- events ----
    async def log_event(self, topic: str, payload: dict) -> None:
        self.s.add(Event(topic=topic, payload=payload))
        await self.s.flush()

    # ---- memories ----
    async def insert_memory(
        self,
        *,
        user_id: str,
        kind: str,
        content: str,
        embedding: list[float],
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
        valid_from: datetime | None = None,
    ) -> MemoryRow:
        kwargs: dict = dict(
            user_id=user_id,
            kind=kind,
            content=content,
            embedding=embedding,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
        )
        if valid_from:
            kwargs["valid_from"] = valid_from
        m = MemoryRow(**kwargs)
        self.s.add(m)
        await self.s.flush()
        return m

    async def search_memories(
        self,
        user_id: str,
        embedding: list[float],
        k: int = 5,
        kinds: list[str] | None = None,
        min_score: float = 0.5,
        active_only: bool = True,
    ) -> list[tuple[MemoryRow, float]]:
        cos_dist = MemoryRow.embedding.cosine_distance(embedding).label("dist")
        q = (
            select(MemoryRow, cos_dist)
            .where(MemoryRow.user_id == user_id)
            .order_by(cos_dist)
            .limit(k * 2)
        )
        if active_only:
            q = q.where(MemoryRow.valid_until.is_(None))
        if kinds:
            q = q.where(MemoryRow.kind.in_(kinds))
        rows = (await self.s.execute(q)).all()
        out: list[tuple[MemoryRow, float]] = []
        for row, dist in rows:
            score = 1.0 - float(dist)
            if score >= min_score:
                out.append((row, score))
            if len(out) >= k:
                break
        return out

    async def find_similar_memory(
        self, user_id: str, kind: str, embedding: list[float], threshold: float
    ) -> MemoryRow | None:
        cos_dist = MemoryRow.embedding.cosine_distance(embedding).label("dist")
        q = (
            select(MemoryRow, cos_dist)
            .where(
                MemoryRow.user_id == user_id,
                MemoryRow.kind == kind,
                MemoryRow.valid_until.is_(None),
            )
            .order_by(cos_dist)
            .limit(1)
        )
        row = (await self.s.execute(q)).first()
        if not row:
            return None
        mem, dist = row
        if (1.0 - float(dist)) >= threshold:
            return mem
        return None

    async def list_active_memories(
        self, user_id: str, kinds: list[str] | None = None
    ) -> list[MemoryRow]:
        q = (
            select(MemoryRow)
            .where(MemoryRow.user_id == user_id, MemoryRow.valid_until.is_(None))
            .order_by(MemoryRow.created_at.desc())
        )
        if kinds:
            q = q.where(MemoryRow.kind.in_(kinds))
        return list((await self.s.execute(q)).scalars().all())

    async def expire_memory(
        self,
        memory_id: str,
        *,
        replaced_by: str | None = None,
        when: datetime | None = None,
    ) -> None:
        ts = when or datetime.now(UTC)
        await self.s.execute(
            update(MemoryRow)
            .where(MemoryRow.id == memory_id, MemoryRow.valid_until.is_(None))
            .values(valid_until=ts, superseded_by=replaced_by)
        )
        await self.s.flush()

    async def get_memory(self, memory_id: str) -> MemoryRow | None:
        return (
            await self.s.execute(select(MemoryRow).where(MemoryRow.id == memory_id))
        ).scalar_one_or_none()

    # ---- knowledge ----
    async def upsert_knowledge_doc(
        self,
        *,
        user_id: str,
        s3_key: str,
        title: str | None,
        content_hash: str,
        metadata: dict,
    ) -> tuple[KnowledgeDoc, bool]:
        """Returns (doc, created_or_changed)."""
        existing = (
            await self.s.execute(
                select(KnowledgeDoc).where(
                    KnowledgeDoc.user_id == user_id,
                    KnowledgeDoc.s3_key == s3_key,
                )
            )
        ).scalar_one_or_none()

        if existing and existing.content_hash == content_hash:
            return existing, False

        if existing:
            existing.content_hash = content_hash
            existing.title = title
            existing.metadata_json = metadata
            await self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == existing.id))
            await self.s.flush()
            return existing, True

        doc = KnowledgeDoc(
            user_id=user_id,
            s3_key=s3_key,
            title=title,
            content_hash=content_hash,
            metadata_json=metadata,
        )
        self.s.add(doc)
        await self.s.flush()
        return doc, True

    async def insert_chunks(self, doc_id: str, chunks: list[tuple[int, str, list[float]]]) -> None:
        for idx, content, emb in chunks:
            self.s.add(
                KnowledgeChunk(doc_id=doc_id, chunk_index=idx, content=content, embedding=emb)
            )
        await self.s.flush()

    async def search_chunks(
        self, user_id: str, embedding: list[float], k: int = 5, min_score: float = 0.5
    ) -> list[tuple[KnowledgeChunk, str, float]]:
        """Returns list of (chunk, doc_title, score)."""
        cos_dist = KnowledgeChunk.embedding.cosine_distance(embedding).label("dist")
        q = (
            select(KnowledgeChunk, KnowledgeDoc.title, cos_dist)
            .join(KnowledgeDoc, KnowledgeDoc.id == KnowledgeChunk.doc_id)
            .where(KnowledgeDoc.user_id == user_id)
            .order_by(cos_dist)
            .limit(k * 2)
        )
        rows = (await self.s.execute(q)).all()
        out = []
        for chunk, title, dist in rows:
            score = 1.0 - float(dist)
            if score >= min_score:
                out.append((chunk, title or "untitled", score))
            if len(out) >= k:
                break
        return out

    # ---- summaries ----
    async def get_summary(self, chat_id: str) -> ChatSummary | None:
        return (
            await self.s.execute(select(ChatSummary).where(ChatSummary.chat_id == chat_id))
        ).scalar_one_or_none()

    async def upsert_summary(self, chat_id: str, summary: str, up_to_message_id: str) -> None:
        existing = await self.get_summary(chat_id)
        if existing:
            existing.summary = summary
            existing.up_to_message_id = up_to_message_id
        else:
            self.s.add(
                ChatSummary(chat_id=chat_id, summary=summary, up_to_message_id=up_to_message_id)
            )
        await self.s.flush()

    # ---- satellites ----
    async def get_or_create_satellite(
        self, satellite_id: str, user_id: str, location: str | None = None
    ) -> Satellite:
        sat = (
            await self.s.execute(select(Satellite).where(Satellite.id == satellite_id))
        ).scalar_one_or_none()
        if sat:
            sat.last_seen_at = datetime.now(UTC)
            return sat
        sat = Satellite(
            id=satellite_id, user_id=user_id, location=location, mode="home_assistant"
        )
        self.s.add(sat)
        await self.s.flush()
        return sat

    async def update_satellite_mode(
        self,
        satellite_id: str,
        mode: str,
        active_chat_id: str | None = None,
        mode_until: datetime | None = None,
    ) -> None:
        await self.s.execute(
            update(Satellite)
            .where(Satellite.id == satellite_id)
            .values(mode=mode, active_chat_id=active_chat_id, mode_until=mode_until)
        )
        await self.s.flush()
