from kasa import Credentials, Device

from ..core.config import settings
from .base import ToolSpec

LAMP_HOST = "192.168.1.26"


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
        creds = Credentials(username=settings.kasa_username, password=settings.kasa_password)
        lamp = await Device.connect(host=LAMP_HOST, credentials=creds)
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
