from kasa import Credentials, Discover

from ..core.config import settings
from .base import ToolSpec

LAMP_HOST = "192.168.1.26"


async def _connect():
    creds = Credentials(username=settings.kasa_username, password=settings.kasa_password)
    dev = await Discover.discover_single(LAMP_HOST, credentials=creds)
    await dev.update()
    return dev


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
        lamp = await _connect()

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
