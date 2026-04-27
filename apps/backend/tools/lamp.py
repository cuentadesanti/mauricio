import asyncio
import logging

from kasa import Credentials, Discover

from ..core.config import settings
from .base import ToolSpec

logger = logging.getLogger(__name__)


async def _connect(creds: Credentials, host: str, max_attempts: int = 3):
    for attempt in range(max_attempts):
        try:
            return await Discover.discover_single(host, credentials=creds)
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            wait = 0.5 * (attempt + 1)
            logger.warning("lamp attempt %d failed: %s — retry in %.1fs", attempt + 1, e, wait)
            await asyncio.sleep(wait)


class LampTool:
    spec = ToolSpec(
        name="lamp",
        description=(
            "Control the smart lamp in the room. "
            "Actions: 'on' turns it on, 'off' turns it off, "
            "'toggle' switches state, 'status' returns current state."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["on", "off", "toggle", "status"],
                    "description": "Action to perform on the lamp.",
                }
            },
            "required": ["action"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        action = args["action"]
        if not settings.lamp_host:
            return {"error": "lamp not configured (LAMP_HOST missing)"}
        creds = Credentials(username=settings.kasa_username, password=settings.kasa_password)
        lamp = await _connect(creds, host=settings.lamp_host)
        try:
            await lamp.update()
            if action == "status":
                return {"is_on": lamp.is_on, "alias": lamp.alias}
            if action == "on":
                await lamp.turn_on()
                return {"is_on": True}
            if action == "off":
                await lamp.turn_off()
                return {"is_on": False}
            if action == "toggle":
                if lamp.is_on:
                    await lamp.turn_off()
                    return {"is_on": False}
                else:
                    await lamp.turn_on()
                    return {"is_on": True}
            return {"error": f"unknown action: {action}"}
        finally:
            await lamp.disconnect()
