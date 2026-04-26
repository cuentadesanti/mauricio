import json

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway
from .memory_service import MemoryService

EXTRACTION_PROMPT = """You extract long-term memories about a user from a recent conversation snippet.

Return a JSON object with three optional arrays:
- "facts": verifiable statements about the user (where they live, what they do, who they know, etc.)
- "preferences": their tastes, opinions, recurring choices
- "entities": named people/places/things that recur in their life and need disambiguation (e.g. "Carla = my partner")

Rules:
- Only extract things explicitly stated by the USER, not the assistant.
- Skip ephemeral details (today's weather, what they're doing right now).
- Each item is a single short sentence in third person ("the user lives in Madrid").
- If nothing useful, return {"facts": [], "preferences": [], "entities": []}.
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
        snippet = f"USER: {user_text}\n\nASSISTANT: {assistant_text}"
        req = CompletionRequest(
            messages=[
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": snippet},
            ],
            model_hint=settings.extractor_model,
            temperature=0.0,
            max_tokens=500,
            metadata={"job": "memory_extraction", "chat_id": chat_id},
        )
        resp = await self.gw.complete(req)
        try:
            content = resp.content.strip()
            # Quitar posibles markdown fences si el modelo se pone rebelde
            if content.startswith("```json"):
                content = content[7:-3].strip()
            elif content.startswith("```"):
                content = content[3:-3].strip()

            extracted = json.loads(content)
        except json.JSONDecodeError:
            return {"stored": 0, "skipped": 0, "error": "bad_json", "raw": resp.content[:200]}

        stored = 0
        skipped = 0
        for kind_key, kind_value in [
            ("facts", "fact"),
            ("preferences", "preference"),
            ("entities", "entity"),
        ]:
            for item in extracted.get(kind_key, []) or []:
                if not isinstance(item, str) or not item.strip():
                    continue
                mid = await self.mem.store_unique(
                    session,
                    user_id=user_id,
                    kind=kind_value,
                    content=item.strip(),
                    source_chat_id=chat_id,
                    source_message_id=source_message_id,
                )
                if mid:
                    stored += 1
                else:
                    skipped += 1

        await session.commit()
        return {"stored": stored, "skipped": skipped}
