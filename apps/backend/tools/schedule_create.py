from datetime import datetime
from zoneinfo import ZoneInfo

from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import Tool, ToolSpec

DEFAULT_TZ = ZoneInfo("Europe/Madrid")
SUPPORTED_KINDS = ("reminder",)


class ScheduleCreateTool(Tool):
    spec = ToolSpec(
        name="schedule_create",
        description=(
            "Schedule something to happen at a future time. Use when the user asks "
            "you to remind them, do something later, or queue an action. The "
            "scheduler runs every minute and dispatches due jobs.\n\n"
            "Supported kinds today:\n"
            " - 'reminder': payload {message} → at run_at, logs an event the user "
            "will see in their next chat / notification channel.\n\n"
            "`run_at` must be ISO-8601. If no timezone is included it is assumed "
            "to be Europe/Madrid (the user's home timezone)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(SUPPORTED_KINDS),
                    "description": "What to do at run_at.",
                },
                "run_at": {
                    "type": "string",
                    "description": (
                        "ISO-8601 datetime, e.g. '2026-04-30T09:00:00' or "
                        "'2026-04-30T09:00:00+02:00'. Naive timestamps are "
                        "interpreted as Europe/Madrid local time."
                    ),
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Kind-specific data. For 'reminder': {message: str}."
                    ),
                },
            },
            "required": ["kind", "run_at", "payload"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        kind = args["kind"]
        if kind not in SUPPORTED_KINDS:
            return {"ok": False, "error": f"unsupported kind: {kind}"}

        try:
            run_at = datetime.fromisoformat(args["run_at"])
        except ValueError as e:
            return {"ok": False, "error": f"bad run_at: {e}"}
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=DEFAULT_TZ)

        payload = args.get("payload") or {}
        if kind == "reminder" and not payload.get("message"):
            return {"ok": False, "error": "reminder payload requires 'message'"}

        async with SessionLocal() as session:
            repo = Repository(session)
            sched = await repo.insert_schedule(
                user_id=ctx["user_id"],
                kind=kind,
                run_at=run_at,
                payload=payload,
            )
            await session.commit()
            return {
                "ok": True,
                "id": sched.id,
                "kind": kind,
                "run_at": run_at.isoformat(),
            }
