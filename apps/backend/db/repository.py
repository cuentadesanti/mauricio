import hashlib
import json

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chat, Event, Message, User


def hash_messages(messages: list[dict]) -> str:
    """Firma estable de un prefix de conversación."""
    canon = json.dumps(
        [{"role": m["role"], "content": m.get("content", "")} for m in messages],
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canon.encode()).hexdigest()


class Repository:
    def __init__(self, session: AsyncSession):
        self.s = session

    # ---- users ----
    async def get_or_create_user(self, handle: str) -> User:
        res = await self.s.execute(select(User).where(User.handle == handle))
        user = res.scalar_one_or_none()
        if user:
            return user
        user = User(handle=handle)
        self.s.add(user)
        await self.s.flush()
        return user

    # ---- chats ----
    async def find_chat_by_signature(self, user_id: str, signature: str) -> Chat | None:
        res = await self.s.execute(
            select(Chat)
            .where(Chat.user_id == user_id, Chat.signature == signature)
            .order_by(desc(Chat.updated_at))
            .limit(1)
        )
        return res.scalar_one_or_none()

    async def create_chat(
        self, user_id: str, channel: str, mode: str, signature: str | None = None
    ) -> Chat:
        chat = Chat(user_id=user_id, channel=channel, mode=mode, signature=signature)
        self.s.add(chat)
        await self.s.flush()
        return chat

    async def update_chat_signature(self, chat: Chat, signature: str) -> None:
        chat.signature = signature
        await self.s.flush()

    # ---- messages ----
    async def add_message(
        self,
        chat_id: str,
        role: str,
        content: dict,
        model: str | None = None,
        token_usage: dict | None = None,
        trace_id: str | None = None,
    ) -> Message:
        msg = Message(
            chat_id=chat_id,
            role=role,
            content=content,
            model=model,
            token_usage=token_usage,
            trace_id=trace_id,
        )
        self.s.add(msg)
        await self.s.flush()
        return msg

    # ---- events ----
    async def log_event(self, topic: str, payload: dict) -> None:
        self.s.add(Event(topic=topic, payload=payload))
        await self.s.flush()
