import asyncio
import json
import time
import uuid
from typing import AsyncIterator

from langfuse.decorators import observe
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.repository import Repository, hash_messages
from ..db.session import SessionLocal
from ..domain.chat import ChatMode
from ..domain.model_gateway import CompletionRequest
from ..gateways.embeddings_gateway import EmbeddingsGateway
from ..gateways.litellm_gateway import LiteLLMGateway
from ..tools.registry import REGISTRY, openai_tool_specs
from .knowledge_service import KnowledgeService
from .memory_extractor import MemoryExtractor
from .memory_service import MemoryService
from .router import pick_model
from .summarizer import Summarizer

MAX_TOOL_LOOPS = 5
RECENT_MESSAGES_WINDOW = 30  # cuántos cargamos al continuar un chat


class ChatService:
    def __init__(self, gateway: LiteLLMGateway):
        self.gw = gateway
        self.emb = EmbeddingsGateway()
        self.knowledge = KnowledgeService(self.emb)
        self.memory = MemoryService(self.emb)
        self.extractor = MemoryExtractor(self.gw, self.memory)
        self.summarizer = Summarizer(self.gw)
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

        # ---- PERSISTENT con retrieval híbrido ----
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

        # ---- construir contexto enriquecido ----
        last_user_text = self._extract_text(incoming_messages[-1])

        # disparar las 3 búsquedas en paralelo
        memories_task = self.memory.retrieve_relevant(session, user.id, last_user_text, k=5)
        chunks_task = self.knowledge.search(session, user.id, last_user_text, k=5)
        summary_task = repo.get_summary(chat.id)

        memories, chunks, summary = await asyncio.gather(
            memories_task, chunks_task, summary_task
        )

        system_msg = self._build_system_prompt(memories, chunks, summary)

        # mensajes raw que mandamos al LLM:
        # si hay summary, solo los últimos 10 de la conversación
        raw_msgs = (
            incoming_messages[-10:] if summary else incoming_messages[-RECENT_MESSAGES_WINDOW:]
        )
        context = [{"role": "system", "content": system_msg}, *raw_msgs]

        async for chunk in self._run_loop(context, user_id=user.id, chat_id=chat.id):
            yield chunk

        # actualizar firma + persistir respuesta del assistant
        full_history = incoming_messages + [{"role": "assistant", "content": self._last_text}]
        new_signature = hash_messages(full_history)
        await repo.update_chat_signature(chat, new_signature)

        assistant_row = await repo.add_message(
            chat.id,
            role="assistant",
            content={"text": self._last_text},
            model=self._last_model,
            token_usage=self._last_usage,
            trace_id=self._last_trace,
        )
        await session.commit()

        # ---- jobs async (no bloquean al usuario, ya respondió) ----
        asyncio.create_task(
            self._post_turn_jobs(
                user_id=user.id,
                chat_id=chat.id,
                user_text=last_user_text,
                assistant_text=self._last_text,
                assistant_msg_id=assistant_row.id,
            )
        )

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

    # ---- helpers nuevos ----

    def _extract_text(self, msg: dict) -> str:
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):  # multimodal
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        return ""

    def _build_system_prompt(self, memories, chunks, summary) -> str:
        parts = ["You are a personal AI assistant. Be concise, accurate, and helpful."]

        if memories:
            mem_lines = "\n".join(f"- ({k}) {c}" for k, c, _ in memories)
            parts.append(f"## What I know about the user\n{mem_lines}")

        if chunks:
            chunk_lines = []
            for title, content, _, _score in chunks:
                snippet = content[:600].strip()
                chunk_lines.append(f"### From: {title}\n{snippet}")
            parts.append(
                "## Relevant excerpts from the user's knowledge base\n" + "\n\n".join(chunk_lines)
            )

        if summary:
            parts.append(f"## Earlier conversation summary\n{summary.summary}")

        return "\n\n".join(parts)

    async def _post_turn_jobs(
        self,
        *,
        user_id: str,
        chat_id: str,
        user_text: str,
        assistant_text: str,
        assistant_msg_id: str,
    ):
        """Corre extracción de memoria + summarization en background.
        Cada job abre su propia session (la del request ya se cerró)."""
        try:
            async with SessionLocal() as s:
                await self.extractor.extract_and_store(
                    s,
                    user_id=user_id,
                    chat_id=chat_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    source_message_id=assistant_msg_id,
                )
            async with SessionLocal() as s:
                await self.summarizer.maybe_summarize(s, chat_id=chat_id)
        except Exception as e:
            print(f"[post_turn_jobs] error: {e}")


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
