import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.repository import Repository
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway
from .memory_service import MemoryService

EXTRACTION_PROMPT = """You maintain a long-term memory store about a user.

You receive:
- The latest exchange between the user and the assistant
- A list of currently active memories about the user (each with an id)

Your job: decide what to add, what to expire, and (when relevant) when each fact became true.

Output a JSON object with these fields, all optional:
{
  "facts":       [{"content": "...", "valid_from": "ISO date or null", "supersedes": ["mem_id", ...]}],
  "preferences": [{"content": "...", "valid_from": null, "supersedes": []}],
  "entities":    [{"content": "...", "valid_from": null, "supersedes": []}],
  "expire":      ["mem_id", ...]
}

Rules:
- Only extract things explicitly stated by the USER, not the assistant.
- Skip ephemeral details (today's weather, current activity).
- Each item is a single short third-person sentence ("the user lives in Lisbon").
- "supersedes" lists active memory ids that this new item REPLACES (e.g. moving city, changing job).
- Use "expire" only for things that became false WITHOUT being replaced.
- "valid_from" only when the user gives a specific date or relative time you can resolve. Otherwise null.
- Output ONLY the JSON object, no commentary, no markdown fences.
"""


class MemoryExtractor:
    def __init__(self, gw: LiteLLMGateway, mem: MemoryService):
        self.gw = gw
        self.mem = mem

    async def extract_and_store(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        chat_id: str,
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
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": snippet},
            ],
            model_hint=settings.extractor_model,
            temperature=0.0,
            max_tokens=800,
            metadata={"job": "memory_extraction", "chat_id": chat_id},
        )
        resp = await self.gw.complete(req)
        try:
            extracted = json.loads(resp.content.strip())
        except json.JSONDecodeError:
            return {"stored": 0, "skipped": 0, "expired": 0, "error": "bad_json"}

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
