from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.repository import Repository
from ..db.session import get_session
from ..gateways.litellm_gateway import LiteLLMGateway
from ..services.chat_service import ChatService
from ..services.voice_orchestrator import VoiceOrchestrator

router = APIRouter()


class VoiceTurnRequest(BaseModel):
    satellite_id: str
    transcript: str


class VoiceTurnResponse(BaseModel):
    text: str
    satellite_mode: str | None = None


def verify_api_key(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != settings.backend_api_key:
        raise HTTPException(401, "invalid api key")


@router.post("/v1/voice/turn", dependencies=[Depends(verify_api_key)])
async def voice_turn(req: VoiceTurnRequest) -> VoiceTurnResponse:
    chat = ChatService(gateway=LiteLLMGateway())
    orch = VoiceOrchestrator(chat_service=chat)
    text = await orch.handle_transcript(
        satellite_id=req.satellite_id,
        transcript=req.transcript,
    )
    return VoiceTurnResponse(text=text)


@router.get(
    "/v1/voice/satellite/{satellite_id}/state", dependencies=[Depends(verify_api_key)]
)
async def satellite_state(
    satellite_id: str, session: AsyncSession = Depends(get_session)  # noqa: B008
):
    repo = Repository(session)
    user = await repo.get_or_create_user(settings.default_user_handle)
    sat = await repo.get_or_create_satellite(satellite_id, user.id)
    await session.commit()
    return {"mode": sat.mode, "active_chat_id": sat.active_chat_id}
