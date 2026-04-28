import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..adapters.whatsapp_evolution import (
    parse_evolution_webhook,
    send_whatsapp_text,
)
from ..core.config import settings
from ..db.session import SessionLocal
from ..domain.chat import ChatMode
from ..gateways.litellm_gateway import LiteLLMGateway
from ..services.chat_service import ChatService

logger = logging.getLogger(__name__)
router = APIRouter()

WHATSAPP_SYSTEM_PROMPT = """You are Mauricio, a personal AI assistant.
The user is talking to you via WhatsApp.

Style — caveman-terse:
- 1–3 lines max unless the user asks for detail.
- Plain text only. No markdown (no **, no #, no bullets).
- No filler, no hedging. Direct.
- Match the user's language and informality.

You have access to the user's full memory and knowledge base.
"""


def _verify_webhook_token(request: Request) -> None:
    if not settings.evolution_webhook_token:
        return  # dev: no auth configured
    token = (
        request.headers.get("authorization", "").removeprefix("Bearer ")
        or request.query_params.get("token", "")
    )
    if token != settings.evolution_webhook_token:
        raise HTTPException(401, "invalid webhook token")


@router.post("/v1/whatsapp/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Recibe eventos de Evolution API."""
    _verify_webhook_token(request)

    body = await request.json()
    logger.info(f"[whatsapp] event={body.get('event', '?')}")

    msg = parse_evolution_webhook(body)
    if not msg:
        return {"ok": True, "ignored": "not_a_text_message"}

    # Opción C: solo respondemos a mensajes que el usuario envió desde su propio WhatsApp
    if not msg.is_from_me:
        logger.info(f"[whatsapp] ignoring inbound from {msg.sender_pushname} (not_from_me)")
        return {"ok": True, "ignored": "not_from_me"}

    if msg.is_group:
        return {"ok": True, "ignored": "group"}

    background_tasks.add_task(
        _process_inbound,
        chat_jid=msg.chat_jid,
        text=msg.text,
        message_id=msg.message_id,
    )
    return {"ok": True, "queued": True}


async def _process_inbound(*, chat_jid: str, text: str, message_id: str) -> None:
    try:
        async with SessionLocal() as session:
            chat_svc = ChatService(gateway=LiteLLMGateway())
            messages = [
                {"role": "system", "content": WHATSAPP_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ]
            chunks: list[str] = []
            async for sse in chat_svc.handle(
                session,
                user_handle=settings.default_user_handle,
                channel="whatsapp",
                mode=ChatMode.PERSISTENT,
                incoming_messages=messages,
                ctx_extra={"external_id": chat_jid},
            ):
                if sse.startswith("data: ") and "[DONE]" not in sse:
                    try:
                        payload = json.loads(sse[6:])
                        delta = payload["choices"][0]["delta"].get("content", "")
                        if delta:
                            chunks.append(delta)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
            await session.commit()

        response_text = "".join(chunks).strip()
        if not response_text:
            return

        await send_whatsapp_text(chat_jid, response_text)
        logger.info(f"[whatsapp] replied to {chat_jid}: {response_text[:80]}")
    except Exception:
        logger.exception(f"[whatsapp] error processing message_id={message_id}")
