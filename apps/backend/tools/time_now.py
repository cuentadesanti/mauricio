from datetime import datetime
from zoneinfo import ZoneInfo

from .base import ToolSpec


class TimeNowTool:
    spec = ToolSpec(
        name="time_now",
        description=(
            "Get the current date and time in a given IANA timezone "
            "(e.g. 'Europe/Madrid', 'America/Mexico_City')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA timezone name. Default 'Europe/Madrid'.",
                    "default": "Europe/Madrid",
                }
            },
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        tz = args.get("timezone") or "Europe/Madrid"
        now = datetime.now(ZoneInfo(tz))
        return {
            "iso": now.isoformat(),
            "human": now.strftime("%A %d %B %Y, %H:%M"),
            "timezone": tz,
        }
