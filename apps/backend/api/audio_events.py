"""Audio-event ingestion endpoint.

Voice satellite (or any other audio frontend) POSTs structured events here
when its local detectors fire — wake word is its own path; this is for the
non-speech detectors (claps, glass break, snap, etc.). The backend decides
how each event maps to action.

Currently implemented:
  - double_clap → toggle the smart lamp (no LLM round-trip)

Authentication piggybacks on the same BACKEND_API_KEY used by /v1/voice/*.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel

from ..core.config import settings
from ..tools.registry import REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter()


class AudioEvent(BaseModel):
    satellite_id: str
    event_type: str
    count: int = 1
    metadata: dict = {}


def verify_api_key(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != settings.backend_api_key:
        raise HTTPException(401, "invalid api key")


@router.post("/v1/audio-event", dependencies=[Depends(verify_api_key)])
async def audio_event(event: AudioEvent, background_tasks: BackgroundTasks):
    print(f"[audio_event] {event.event_type} from {event.satellite_id}", flush=True)
    background_tasks.add_task(_dispatch, event)
    return {"ok": True}


async def _dispatch(event: AudioEvent) -> None:
    """Map audio event → side effect. Hardcoded routes today; eventually
    these should live in DB/config so the user can rebind without code."""
    if event.event_type == "double_clap":
        lamp = REGISTRY.get("lamp")
        if not lamp:
            print("[audio_event] double_clap fired but lamp tool not registered "
                  "(missing KASA creds or LAMP_HOST?)", flush=True)
            return
        try:
            result = await lamp.run({"action": "toggle"}, ctx={})
            print(f"[audio_event] double_clap → lamp toggle: {result}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[audio_event] lamp toggle failed: {e}", flush=True)
            logger.exception("lamp toggle from double_clap failed")
        return

    print(f"[audio_event] no handler for event_type={event.event_type}", flush=True)
