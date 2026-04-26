import json
import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from ..core.config import settings
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway

router = APIRouter()
gateway = LiteLLMGateway()


def verify_api_key(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization[7:] != settings.backend_api_key:
        raise HTTPException(401, "invalid api key")


@router.get("/models")
async def list_models(_: None = Depends(verify_api_key)):
    """LibreChat consulta esto al arrancar."""
    return {
        "object": "list",
        "data": [
            {"id": "personal-ai-default", "object": "model", "owned_by": "you"},
        ],
    }


@router.post("/chat/completions", dependencies=[Depends(verify_api_key)])
async def chat_completions(body: dict):
    req = CompletionRequest(
        messages=body["messages"],
        model_hint=body.get("model") if body.get("model") != "personal-ai-default" else None,
        tools=body.get("tools"),
        temperature=body.get("temperature", 0.7),
        max_tokens=body.get("max_tokens"),
        metadata={"source": "librechat"},
    )

    if body.get("stream"):
        return StreamingResponse(_stream(req), media_type="text/event-stream")

    resp = await gateway.complete(req)
    return _format_response(resp)


async def _stream(req: CompletionRequest):
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    async for chunk in gateway.stream(req):
        # LiteLLM ya devuelve formato OpenAI, solo lo serializamos como SSE
        delta = chunk.choices[0].delta
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": chunk.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": delta.role,
                        "content": delta.content or "",
                    },
                    "finish_reason": chunk.choices[0].finish_reason,
                }
            ],
        }
        yield f"data: {json.dumps(payload)}\n\n"
    yield "data: [DONE]\n\n"


def _format_response(resp):
    return {
        "id": f"chatcmpl-{resp.trace_id or uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": resp.model_used,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": resp.content,
                    "tool_calls": resp.tool_calls or None,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": resp.usage,
    }
