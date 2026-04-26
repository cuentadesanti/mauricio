from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.repository import Repository
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway

SUMMARY_PROMPT = """You compact a long conversation into a concise summary.

Goal: capture WHO did/said WHAT and any DECISIONS, OPEN QUESTIONS, and PERSISTENT FACTS that may matter for future turns.

Constraints:
- Max ~400 words.
- Use bullet points or short sentences.
- Skip greetings, filler, repetitive content.
- If a previous summary is provided, integrate it; do not duplicate.
- Output ONLY the summary text. No headers, no preamble.
"""

# Política: si tenemos > THRESHOLD mensajes y el summary actual está más de DRIFT mensajes atrás,
# regeneramos cubriendo hasta -KEEP_RAW.
THRESHOLD_MSG_COUNT = 20
DRIFT_MAX = 10
KEEP_RAW = 10


class Summarizer:
    def __init__(self, gw: LiteLLMGateway):
        self.gw = gw

    async def maybe_summarize(self, session: AsyncSession, *, chat_id: str) -> bool:
        repo = Repository(session)
        msgs = await repo.get_messages(chat_id, limit=200)
        if len(msgs) < THRESHOLD_MSG_COUNT:
            return False

        existing = await repo.get_summary(chat_id)

        # Decidir si necesita refrescarse
        if existing and existing.up_to_message_id:
            covered_index = next(
                (i for i, m in enumerate(msgs) if m.id == existing.up_to_message_id), -1
            )
            uncovered_recent = len(msgs) - 1 - covered_index
            if uncovered_recent <= DRIFT_MAX:
                return False
        # punto de corte: todo menos los últimos KEEP_RAW
        cutoff = max(0, len(msgs) - KEEP_RAW)
        to_summarize = msgs[:cutoff]
        if not to_summarize:
            return False

        prior_summary = existing.summary if existing else ""
        block = "\n".join(
            f"{m.role.upper()}: {(m.content or {}).get('text','')}" for m in to_summarize
        )

        req = CompletionRequest(
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {
                    "role": "user",
                    "content": (
                        (f"PREVIOUS SUMMARY:\n{prior_summary}\n\n" if prior_summary else "")
                        + f"NEW CONVERSATION SO FAR:\n{block}"
                    ),
                },
            ],
            model_hint=settings.extractor_model,
            temperature=0.0,
            max_tokens=800,
            metadata={"job": "summarization", "chat_id": chat_id},
        )
        resp = await self.gw.complete(req)
        await repo.upsert_summary(
            chat_id=chat_id,
            summary=resp.content.strip(),
            up_to_message_id=msgs[cutoff - 1].id,
        )
        await session.commit()
        return True
