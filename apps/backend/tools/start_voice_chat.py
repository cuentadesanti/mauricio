from datetime import UTC, datetime, timedelta

from ..core.config import settings
from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import ToolSpec


class StartVoiceChatTool:
    contexts = ("voice",)
    spec = ToolSpec(
        name="start_voice_chat",
        description=(
            "Start an extended voice conversation with the user. "
            "Call this when the user asks to chat, talk longer, or start a conversation. "
            "Only meaningful when the request comes from a voice satellite."
        ),
        parameters={
            "type": "object",
            "properties": {
                "intro": {
                    "type": "string",
                    "description": "Short greeting line to say back, e.g. 'Sure, what's up?'.",
                }
            },
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        satellite_id = ctx.get("satellite_id")
        if not satellite_id:
            return {"ok": False, "error": "not a voice context"}
        intro = args.get("intro", "Okay, I'm listening.")

        async with SessionLocal() as session:
            repo = Repository(session)
            user = await repo.get_or_create_user(settings.default_user_handle)
            chat = await repo.create_chat(user.id, channel="voice", mode="persistent")
            await repo.update_satellite_mode(
                satellite_id,
                "voice_chat",
                chat.id,
                datetime.now(UTC) + timedelta(seconds=90),
            )
            await session.commit()
            return {"ok": True, "chat_id": chat.id, "say": intro}
