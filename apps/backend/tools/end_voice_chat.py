from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import ToolSpec


class EndVoiceChatTool:
    spec = ToolSpec(
        name="end_voice_chat",
        description=(
            "End the current extended voice conversation and return to home_assistant mode. "
            "Call this when the user says they're done chatting, want to stop, or close the conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "farewell": {
                    "type": "string",
                    "description": "Short closing line, e.g. 'Talk to you later.'",
                }
            },
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        satellite_id = ctx.get("satellite_id")
        if not satellite_id:
            return {"ok": False, "error": "not a voice context"}
        farewell = args.get("farewell", "Okay.")

        async with SessionLocal() as session:
            repo = Repository(session)
            await repo.update_satellite_mode(satellite_id, "home_assistant", None, None)
            await session.commit()
            return {"ok": True, "say": farewell}
