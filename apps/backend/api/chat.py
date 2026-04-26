from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..db.session import get_session
from ..domain.chat import ChatMode
from ..gateways.litellm_gateway import LiteLLMGateway
from ..services.chat_service import ChatService

router = APIRouter()

# Convención: dos "modelos" expuestos a LibreChat:
#   personal-ai        → modo persistent
#   personal-ai-quick  → modo memoryless
MODEL_TO_MODE = {
    "personal-ai": ChatMode.PERSISTENT,
    "personal-ai-quick": ChatMode.MEMORYLESS,
}


def verify_api_key(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != settings.backend_api_key:
        raise HTTPException(401, "invalid api key")


@router.get("/models")
async def list_models(_: None = Depends(verify_api_key)):
    return {
        "object": "list",
        "data": [
            {"id": "personal-ai", "object": "model", "owned_by": "you"},
            {"id": "personal-ai-quick", "object": "model", "owned_by": "you"},
        ],
    }


@router.post("/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(
    body: dict, session: AsyncSession = Depends(get_session)
):  # noqa: B008
    model_id = body.get("model", "personal-ai")
    mode = MODEL_TO_MODE.get(model_id, ChatMode.PERSISTENT)

    service = ChatService(gateway=LiteLLMGateway())

    # de momento siempre stream — LibreChat siempre lo pide
    async def gen():
        async for chunk in service.handle(
            session,
            user_handle=settings.default_user_handle,
            channel="web",
            mode=mode,
            incoming_messages=body["messages"],
        ):
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream")
