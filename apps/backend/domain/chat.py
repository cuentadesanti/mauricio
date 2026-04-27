from enum import StrEnum

from pydantic import BaseModel


class ChatMode(StrEnum):
    MEMORYLESS = "memoryless"
    PERSISTENT = "persistent"
    HOME_ASSISTANT = "home_assistant"


class InboundMessage(BaseModel):
    channel: str
    user_handle: str
    messages: list[dict]  # OpenAI format
    chat_id: str | None = None
    mode: ChatMode = ChatMode.PERSISTENT
    metadata: dict = {}
