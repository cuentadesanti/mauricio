from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.json_utils import parse_json_lenient
from ..core.prompts import load_prompt
from ..db.repository import Repository
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway
from .memory_service import MemoryService


class MemoryExtractor:
    def __init__(self, gw: LiteLLMGateway, mem: MemoryService):
        self.gw = gw
        self.mem = mem

    async def extract_and_store(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        chat_id: str | None,
        user_text: str,
        assistant_text: str,
        source_message_id: str | None = None,
    ) -> dict:
        repo = Repository(session)

        active = await repo.list_active_memories(user_id)
        active_block = "\n".join(
            f"- [{m.id}] ({m.kind}) {m.content}" for m in active[:50]
        )

        snippet = (
            f"ACTIVE MEMORIES:\n{active_block or '(none)'}\n\n"
            f"USER: {user_text}\n\n"
            f"ASSISTANT: {assistant_text}"
        )
        req = CompletionRequest(
            messages=[
                {"role": "system", "content": load_prompt("memory_extraction")},
                {"role": "user", "content": snippet},
            ],
            model_hint=settings.extractor_model,
            temperature=0.0,
            max_tokens=800,
            metadata={"job": "memory_extraction", "chat_id": chat_id or "no-chat"},
            response_format={"type": "json_object"},
        )
        resp = await self.gw.complete(req)
        extracted = parse_json_lenient(resp.content)
        if extracted is None:
            return {
                "stored": 0,
                "skipped": 0,
                "expired": 0,
                "error": "bad_json",
                "raw_head": (resp.content or "")[:200],
            }

        stats = {"stored": 0, "skipped": 0, "expired": 0}

        for kind_key, kind_value in [
            ("facts", "fact"),
            ("preferences", "preference"),
            ("entities", "entity"),
        ]:
            for item in extracted.get(kind_key, []) or []:
                if not isinstance(item, dict) or not item.get("content", "").strip():
                    continue
                content = item["content"].strip()
                supersedes = item.get("supersedes") or []
                valid_from = self._parse_date(item.get("valid_from"))

                new_id = await self.mem.store_unique(
                    session,
                    user_id=user_id,
                    kind=kind_value,
                    content=content,
                    source_chat_id=chat_id,
                    source_message_id=source_message_id,
                    valid_from=valid_from,
                )
                if not new_id:
                    stats["skipped"] += 1
                    continue
                stats["stored"] += 1

                for old_id in supersedes:
                    await repo.expire_memory(old_id, replaced_by=new_id)
                    stats["expired"] += 1

        for old_id in extracted.get("expire", []) or []:
            await repo.expire_memory(old_id, replaced_by=None)
            stats["expired"] += 1

        await session.commit()
        return stats

    def _parse_date(self, raw) -> datetime | None:
        if not raw or not isinstance(raw, str):
            return None
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
