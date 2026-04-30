import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from ..adapters.whatsapp_evolution import (
    parse_evolution_webhook,
    send_whatsapp_presence,
    send_whatsapp_text,
)
from ..core.config import settings
from ..core.prompts import load_prompt
from ..db.session import SessionLocal
from ..domain.chat import ChatMode
from ..gateways.litellm_gateway import LiteLLMGateway
from ..services.chat_service import ChatService

logger = logging.getLogger(__name__)
router = APIRouter()


@contextlib.asynccontextmanager
async def typing_presence(chat_jid: str, refresh_every: float = 8.0):
    """Keep a WhatsApp 'composing' indicator alive for the duration of a tool
    loop. WhatsApp clears the typing animation after ~10s, so we refresh
    every ~8s. Cancels cleanly on exit (so the indicator stops once we send
    the actual reply)."""

    async def _refresh():
        while True:
            await send_whatsapp_presence(chat_jid, "composing")
            await asyncio.sleep(refresh_every)

    task = asyncio.create_task(_refresh())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


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

    # Single-JID lock: when whatsapp_only_jid is set, drop everything else.
    # Removing the old `is_group` block deliberately — a one-person group is
    # the canonical setup, and that block was blocking it. The JID filter
    # below replaces it. CAVEAT: if whatsapp_only_jid is unset and is_group
    # check is also gone, the bot replies to any group you post in. Keep
    # whatsapp_only_jid set in production.
    if settings.whatsapp_only_jid and msg.chat_jid != settings.whatsapp_only_jid:
        logger.info(f"[whatsapp] ignoring jid={msg.chat_jid} (not_target_jid)")
        return {"ok": True, "ignored": "not_target_jid"}

    background_tasks.add_task(
        _process_inbound,
        chat_jid=msg.chat_jid,
        text=msg.text,
        message_id=msg.message_id,
    )
    return {"ok": True, "queued": True}


async def _process_inbound(*, chat_jid: str, text: str, message_id: str) -> None:
    try:
        async with SessionLocal() as session, typing_presence(chat_jid):
            chat_svc = ChatService(gateway=LiteLLMGateway())
            messages = [
                {"role": "system", "content": load_prompt("whatsapp")},
                {"role": "user", "content": text},
            ]
            chunks: list[str] = []
            tools_used: list[str] = []
            async for sse in chat_svc.handle(
                session,
                user_handle=settings.default_user_handle,
                channel="whatsapp",
                mode=ChatMode.PERSISTENT,
                incoming_messages=messages,
                ctx_extra={"external_id": chat_jid},
            ):
                if not sse.startswith("data: ") or "[DONE]" in sse:
                    continue
                try:
                    payload = json.loads(sse[6:])
                except json.JSONDecodeError:
                    continue
                # x_meta event carries tool list (see _fake_stream)
                if "x_meta" in payload:
                    tools_used = payload["x_meta"].get("tools_used", []) or []
                    continue
                try:
                    delta = payload["choices"][0]["delta"].get("content", "")
                    if delta:
                        chunks.append(delta)
                except (KeyError, IndexError):
                    pass
            await session.commit()

        response_text = "".join(chunks).strip()
        if not response_text:
            return

        # Conditional footer: only when tools were actually used. Italic markdown
        # in WhatsApp wraps with single underscores around a sentence.
        if tools_used:
            response_text = f"{response_text}\n\n_🔧 {', '.join(tools_used)}_"

        await send_whatsapp_text(chat_jid, response_text)
        print(f"[whatsapp] replied to {chat_jid} tools={tools_used}: {response_text[:80]}",
              flush=True)
    except Exception as e:
        print(f"[whatsapp] error processing message_id={message_id}: {e}", flush=True)
        logger.exception(f"[whatsapp] error processing message_id={message_id}")
