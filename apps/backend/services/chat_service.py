import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

from langfuse.decorators import observe
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.repository import Repository, hash_messages
from ..domain.chat import ChatMode
from ..domain.model_gateway import CompletionRequest
from ..gateways.litellm_gateway import LiteLLMGateway
from ..tools.registry import REGISTRY, openai_tool_specs
from .router import pick_model

MAX_TOOL_LOOPS = 5
RECENT_MESSAGES_WINDOW = 30  # cuántos cargamos al continuar un chat


class ChatService:
    def __init__(self, gateway: LiteLLMGateway):
        self.gw = gateway
        self._last_text = ""
        self._last_model = ""
        self._last_usage = {}
        self._last_trace = ""

    async def handle(
        self,
        session: AsyncSession,
        *,
        user_handle: str,
        channel: str,
        mode: ChatMode,
        incoming_messages: list[dict],
    ) -> AsyncIterator[str]:
        """Yields SSE-formatted chunks. La API endpoint solo los serializa."""
        repo = Repository(session)
        user = await repo.get_or_create_user(user_handle)

        if mode == ChatMode.MEMORYLESS:
            await repo.log_event(
                "chat.memoryless.in", {"user": user.id, "messages": incoming_messages}
            )
            async for chunk in self._run_loop(incoming_messages, user_id=user.id, chat_id=None):
                yield chunk
            await session.commit()
            return

        # ---- PERSISTENT ----
        # firma del prefix para reconciliar el chat
        prior = incoming_messages[:-1]
        signature_in = hash_messages(prior) if prior else ""

        chat = (
            await repo.find_chat_by_signature(user.id, signature_in) if signature_in else None
        )
        if not chat:
            chat = await repo.create_chat(user.id, channel=channel, mode=mode.value)
            # persistimos toda la historia recibida (es la primera vez que la vemos)
            for m in incoming_messages:
                await repo.add_message(
                    chat.id, role=m["role"], content={"text": m.get("content", "")}
                )
        else:
            # continuación: solo persistimos el nuevo user message
            new_user_msg = incoming_messages[-1]
            await repo.add_message(
                chat.id,
                role=new_user_msg["role"],
                content={"text": new_user_msg.get("content", "")},
            )

        # construimos el contexto que mandamos al LLM
        context = incoming_messages[-RECENT_MESSAGES_WINDOW:]

        async for chunk in self._run_loop(context, user_id=user.id, chat_id=chat.id):
            yield chunk

        # actualizar firma del chat: ahora incluye user + assistant nuevos
        full_history = incoming_messages + [{"role": "assistant", "content": self._last_text}]
        new_signature = hash_messages(full_history)
        await repo.update_chat_signature(chat, new_signature)

        # persistir respuesta del assistant
        await repo.add_message(
            chat.id,
            role="assistant",
            content={"text": self._last_text},
            model=self._last_model,
            token_usage=self._last_usage,
            trace_id=self._last_trace,
        )
        await session.commit()

    # --- loop interno con tools ---

    @observe(name="tool_loop")
    async def _collect_response(
        self, messages: list[dict], *, user_id: str, chat_id: str | None
    ) -> None:
        """Corre el loop LLM → tools hasta obtener respuesta final. Guarda resultado en self._last_*."""
        ctx = {"user_id": user_id, "chat_id": chat_id}
        working = list(messages)

        for _ in range(MAX_TOOL_LOOPS):
            req = CompletionRequest(
                messages=working,
                model_hint=pick_model(working),
                tools=openai_tool_specs() or None,
                metadata={"chat_id": chat_id, "user_id": user_id},
            )
            resp = await self.gw.complete(req)

            if not resp.tool_calls:
                self._last_text = resp.content
                self._last_model = resp.model_used
                self._last_usage = resp.usage
                self._last_trace = resp.trace_id
                return

            assistant_msg = {
                "role": "assistant",
                "content": resp.content or None,
                "tool_calls": resp.tool_calls,
            }
            working.append(assistant_msg)

            for tc in resp.tool_calls:
                fn = tc["function"]
                tool_name = fn["name"]
                args = json.loads(fn.get("arguments", "{}") or "{}")
                tool = REGISTRY.get(tool_name)
                if not tool:
                    result = {"error": f"unknown tool {tool_name}"}
                else:
                    try:
                        result = await tool.run(args, ctx)
                    except Exception as e:
                        result = {"error": str(e)}
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        self._last_text = "[stopped: too many tool iterations]"
        self._last_model = "n/a"
        self._last_usage = {}
        self._last_trace = ""

    async def _run_loop(
        self, messages: list[dict], *, user_id: str, chat_id: str | None
    ) -> AsyncIterator[str]:
        await self._collect_response(messages, user_id=user_id, chat_id=chat_id)
        async for sse in _fake_stream(self._last_text, self._last_model):
            yield sse


async def _fake_stream(text: str, model: str, chunk_size: int = 20):
    """Convierte un string en chunks SSE para que LibreChat lo muestre fluido."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": chunk},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.015)
    # mensaje final con finish_reason
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"
