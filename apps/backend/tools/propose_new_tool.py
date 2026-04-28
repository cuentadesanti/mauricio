import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import ToolSpec

logger = logging.getLogger(__name__)


class ProposeNewToolTool:
    contexts = ("web",)  # only available from LibreChat, not voice/WhatsApp

    spec = ToolSpec(
        name="propose_new_tool",
        description=(
            "Propose a new tool that the assistant should have. "
            "Use when the user explicitly asks for a new capability that doesn't exist yet "
            "(e.g. 'I want a tool that sends SMS', 'add a calculator', 'I need to control my Spotify'). "
            "This will trigger a feasibility analysis and may open a Pull Request on GitHub. "
            "DO NOT use this for tools that already exist or for one-off tasks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short tool name in snake_case, e.g. 'send_sms'",
                },
                "summary": {
                    "type": "string",
                    "description": "One-line summary of what the tool does.",
                },
                "use_cases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 concrete examples of when this tool would be invoked.",
                },
                "external_apis": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "External APIs/services needed, if any (e.g. 'Twilio', 'OpenWeather').",
                },
            },
            "required": ["title", "summary", "use_cases"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        title = args["title"]
        summary = args["summary"]
        use_cases = args.get("use_cases", [])
        external_apis = args.get("external_apis", [])

        request_id = str(uuid4())

        async with SessionLocal() as session:
            repo = Repository(session)
            await repo.log_event(
                "feature_request.received",
                {
                    "request_id": request_id,
                    "title": title,
                    "summary": summary,
                    "use_cases": use_cases,
                    "external_apis": external_apis,
                    "user_id": ctx.get("user_id"),
                    "chat_id": ctx.get("chat_id"),
                    "received_at": datetime.now(UTC).isoformat(),
                },
            )
            await session.commit()

        # Run triage async — do not block the user's response
        from ..services.feature_request_service import FeatureRequestService

        asyncio.create_task(
            FeatureRequestService().handle_request(
                request_id=request_id,
                title=title,
                summary=summary,
                use_cases=use_cases,
                external_apis=external_apis,
            )
        )

        return {
            "ok": True,
            "request_id": request_id,
            "say": (
                f"Got it. I'm analyzing whether '{title}' is feasible. "
                "I'll let you know my verdict in a minute."
            ),
        }
