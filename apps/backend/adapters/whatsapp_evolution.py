"""Adapter para Evolution API (WhatsApp).

Normaliza webhooks entrantes a InboundWhatsAppMessage
y envía respuestas de vuelta via Evolution REST API.
"""

import logging
from typing import Any

import httpx
from pydantic import BaseModel

from ..core.config import settings

logger = logging.getLogger(__name__)


class InboundWhatsAppMessage(BaseModel):
    instance: str
    chat_jid: str        # '5551234567@s.whatsapp.net' o '...@g.us' para grupos
    sender_jid: str      # quien lo manda (distinto a chat_jid en grupos)
    sender_pushname: str | None
    is_group: bool
    is_from_me: bool     # True si lo mandé yo desde mi WhatsApp
    text: str
    message_id: str
    timestamp: int


def parse_evolution_webhook(body: dict) -> InboundWhatsAppMessage | None:
    """Parsea payload Evolution v2 (event: messages.upsert). Retorna None si no es procesable."""
    if body.get("event") != "messages.upsert":
        return None

    data = body.get("data", {})
    if not data:
        return None

    key = data.get("key", {})
    chat_jid = key.get("remoteJid")
    if not chat_jid:
        return None

    is_from_me = bool(key.get("fromMe", False))
    message_id = key.get("id", "")

    msg = data.get("message", {})
    text = (
        msg.get("conversation")
        or msg.get("extendedTextMessage", {}).get("text")
        or ""
    ).strip()
    if not text:
        return None

    is_group = chat_jid.endswith("@g.us")
    sender_jid = key.get("participant") or chat_jid

    return InboundWhatsAppMessage(
        instance=body.get("instance", ""),
        chat_jid=chat_jid,
        sender_jid=sender_jid,
        sender_pushname=data.get("pushName"),
        is_group=is_group,
        is_from_me=is_from_me,
        text=text,
        message_id=message_id,
        timestamp=int(data.get("messageTimestamp", 0)),
    )


async def send_whatsapp_text(chat_jid: str, text: str) -> dict[str, Any]:
    """Envía un mensaje de texto via Evolution API."""
    if not settings.evolution_api_url or not settings.evolution_api_key:
        raise RuntimeError("Evolution API not configured")

    url = (
        f"{settings.evolution_api_url.rstrip('/')}"
        f"/message/sendText/{settings.evolution_instance}"
    )
    payload = {
        "number": chat_jid.replace("@s.whatsapp.net", "").replace("@g.us", ""),
        "text": text,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            url,
            headers={"apikey": settings.evolution_api_key, "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()
