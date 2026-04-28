import json
from datetime import UTC, datetime, timedelta

from ..core.config import settings
from ..db.repository import Repository
from ..db.session import SessionLocal
from ..domain.chat import ChatMode
from .chat_service import ChatService

HOME_ASSISTANT_PROMPT = """You are Mauricio, a voice assistant. User talks via microphone in their home.

Style — be caveman-terse:
- 1 sentence max. 2 only if truly unavoidable.
- No filler, no hedging, no pleasantries. Spoken words only — no markdown.
- Tool confirmed action → single word or fragment ("Done." "Light on." "No results.").
- Complex answer → one-line gist, offer to open chat.
- Match user's language.

Mode: home_assistant — each turn is a one-off command.
- User wants longer chat → call `start_voice_chat`.
- User wants to end conversation → call `end_voice_chat` (voice_chat mode only).
"""

VOICE_CHAT_PROMPT = """You are Mauricio, a voice assistant in extended conversation.

Style — caveman-terse:
- Max 2 sentences unless user explicitly asks for more.
- No markdown, no bullets — spoken words only.
- No filler. No hedging. Direct.

User exits by saying "end conversation", "that's all", or 90 seconds of silence.
"""

VOICE_CHAT_TIMEOUT = timedelta(seconds=90)


class VoiceOrchestrator:
    """
    Punto de entrada para cualquier transcript que viene de un satélite.
    Decide modo, llama a ChatService, devuelve texto para TTS.
    """

    def __init__(self, chat_service: ChatService):
        self.chat = chat_service

    async def handle_transcript(
        self, *, satellite_id: str, transcript: str
    ) -> str:
        """Ejecuta un turn de voz. Devuelve el texto que se va a hablar."""
        if not transcript.strip():
            return ""

        async with SessionLocal() as session:
            repo = Repository(session)
            user = await repo.get_or_create_user(settings.default_user_handle)
            sat = await repo.get_or_create_satellite(satellite_id, user.id)
            await session.commit()

            # ¿voice_chat expirado por timeout?
            now = datetime.now(UTC)
            if sat.mode == "voice_chat" and sat.mode_until and now > sat.mode_until:
                await repo.update_satellite_mode(satellite_id, "home_assistant", None, None)
                await session.commit()
                sat.mode = "home_assistant"
                sat.active_chat_id = None

            mode_now = sat.mode
            active_chat_id = sat.active_chat_id

        # Construye el "fake messages array" como si viniera de LibreChat
        if mode_now == "voice_chat" and active_chat_id:
            response_text = await self._handle_voice_chat(
                satellite_id, active_chat_id, transcript
            )
        else:
            response_text = await self._handle_home_assistant(satellite_id, transcript)

        return response_text

    async def _handle_home_assistant(self, satellite_id: str, transcript: str) -> str:
        """Reusa ChatService.handle pero acumulando los chunks en lugar de streamearlos."""
        messages = [
            {"role": "system", "content": HOME_ASSISTANT_PROMPT},
            {"role": "user", "content": transcript},
        ]

        async with SessionLocal() as session:
            chunks: list[str] = []
            async for sse in self.chat.handle(
                session,
                user_handle=settings.default_user_handle,
                channel="voice",
                mode=ChatMode.HOME_ASSISTANT,
                incoming_messages=messages,
                ctx_extra={"satellite_id": satellite_id},
            ):
                text = _extract_text_from_sse(sse)
                if text:
                    chunks.append(text)
            await session.commit()

        return "".join(chunks).strip()

    async def _handle_voice_chat(
        self, satellite_id: str, chat_id: str, transcript: str
    ) -> str:
        """Modo conversación: persistencia real, sigue el chat existente.
        MM-5: single session for history load + LLM turn + timeout extension."""
        async with SessionLocal() as session:
            repo = Repository(session)
            db_messages = await repo.get_messages(chat_id, limit=30)

            history = [
                {"role": m.role, "content": (m.content or {}).get("text", "")}
                for m in db_messages
            ]
            messages = (
                [{"role": "system", "content": VOICE_CHAT_PROMPT}]
                + history
                + [{"role": "user", "content": transcript}]
            )

            chunks: list[str] = []
            async for sse in self.chat.handle(
                session,
                user_handle=settings.default_user_handle,
                channel="voice",
                mode=ChatMode.PERSISTENT,
                incoming_messages=messages,
                ctx_extra={"satellite_id": satellite_id, "force_chat_id": chat_id},
            ):
                text = _extract_text_from_sse(sse)
                if text:
                    chunks.append(text)

            # extend timeout within the same session
            await repo.update_satellite_mode(
                satellite_id,
                "voice_chat",
                chat_id,
                datetime.now(UTC) + VOICE_CHAT_TIMEOUT,
            )
            await session.commit()

        return "".join(chunks).strip()


def _extract_text_from_sse(sse: str) -> str:
    if not sse.startswith("data: "):
        return ""
    payload = sse[6:].strip()
    if payload == "[DONE]":
        return ""
    try:
        obj = json.loads(payload)
        return obj["choices"][0].get("delta", {}).get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError):
        return ""
