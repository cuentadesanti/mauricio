import json
import uuid
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
    body: dict, session: AsyncSession = Depends(get_session)  # noqa: B008
):
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


def _content_to_str(content) -> str:
    """Normaliza el campo content del Responses API a string plano."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text")
        )
    return str(content)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/responses", dependencies=[Depends(verify_api_key)])
async def responses_api(
    body: dict, session: AsyncSession = Depends(get_session)  # noqa: B008
):
    """Compatibilidad con la OpenAI Responses API (usada por LibreChat Agents)."""
    model_id = body.get("model", "personal-ai")
    mode = MODEL_TO_MODE.get(model_id, ChatMode.PERSISTENT)

    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        messages = [{"role": "user", "content": raw_input}]
    else:
        messages = [
            {"role": m["role"], "content": _content_to_str(m.get("content", ""))}
            for m in raw_input
            if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system")
        ]

    if instructions := body.get("instructions"):
        messages = [{"role": "system", "content": instructions}] + messages

    service = ChatService(gateway=LiteLLMGateway())
    resp_id = f"resp_{uuid.uuid4().hex}"
    msg_id = f"msg_{uuid.uuid4().hex[:8]}"

    async def gen():
        yield _sse(
            "response.created",
            {
                "type": "response.created",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "status": "in_progress",
                    "model": model_id,
                    "output": [],
                },
            },
        )
        yield _sse(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "message", "role": "assistant", "content": [], "id": msg_id},
            },
        )
        yield _sse(
            "response.content_part.added",
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": ""},
            },
        )

        full_text = ""
        async for chunk in service.handle(
            session,
            user_handle=settings.default_user_handle,
            channel="web",
            mode=mode,
            incoming_messages=messages,
        ):
            if not chunk.startswith("data: ") or "[DONE]" in chunk:
                continue
            try:
                data = json.loads(chunk[6:])
                delta = data["choices"][0]["delta"].get("content", "")
                if delta:
                    full_text += delta
                    yield _sse(
                        "response.output_text.delta",
                        {
                            "type": "response.output_text.delta",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": delta,
                        },
                    )
            except (json.JSONDecodeError, IndexError, KeyError):
                pass

        output_item = {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": full_text}],
        }
        yield _sse(
            "response.output_text.done",
            {
                "type": "response.output_text.done",
                "output_index": 0,
                "content_index": 0,
                "text": full_text,
            },
        )
        yield _sse(
            "response.output_item.done",
            {"type": "response.output_item.done", "output_index": 0, "item": output_item},
        )
        yield _sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": resp_id,
                    "object": "response",
                    "status": "completed",
                    "model": model_id,
                    "output": [output_item],
                    "usage": {},
                },
            },
        )

    return StreamingResponse(gen(), media_type="text/event-stream")
