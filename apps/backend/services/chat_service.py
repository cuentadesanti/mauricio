import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

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


@dataclass
class _LoopResult:
    """CR-2: return value from _collect_response — no shared mutable state."""

    text: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    trace_id: str = ""


class ChatService:
    def __init__(self, gateway: LiteLLMGateway):
        self.gw = gateway
        self.emb = EmbeddingsGateway()
        self.knowledge = KnowledgeService(self.emb)
        self.memory = MemoryService(self.emb)
        self.extractor = MemoryExtractor(self.gw, self.memory)
        self.summarizer = Summarizer(self.gw)

    async def handle(
        self,
        session: AsyncSession,
        *,
        user_handle: str,
        channel: str,
        mode: ChatMode,
        incoming_messages: list[dict],
        ctx_extra: dict | None = None,
    ) -> AsyncIterator[str]:
        """Yields SSE-formatted chunks. La API endpoint solo los serializa."""
        repo = Repository(session)
        user = await repo.get_or_create_user(user_handle)

        if mode == ChatMode.MEMORYLESS:
            await repo.log_event(
                "chat.memoryless.in", {"user": user.id, "messages": incoming_messages}
            )
            result = await self._collect_response(
                incoming_messages, user_id=user.id, chat_id=None,
                channel=channel, ctx_extra=ctx_extra
            )
            async for sse in _fake_stream(result.text, result.model):
                yield sse
            await session.commit()
            return

        # ---- HOME_ASSISTANT: stateless pero con memoria y knowledge ----
        if mode == ChatMode.HOME_ASSISTANT:
            last_user_text = self._extract_text(incoming_messages[-1])

            memories_task = self.memory.retrieve_relevant(session, user.id, last_user_text, k=5)
            chunks_task = self.knowledge.search(session, user.id, last_user_text, k=3)
            memories, chunks = await asyncio.gather(memories_task, chunks_task)

            base_system = (
                incoming_messages[0]["content"]
                if incoming_messages and incoming_messages[0]["role"] == "system"
                else ""
            )
            enriched_system = self._build_voice_system_prompt(
                base_system=base_system, memories=memories, chunks=chunks
            )
            raw_msgs = [m for m in incoming_messages if m["role"] != "system"]
            context = [{"role": "system", "content": enriched_system}, *raw_msgs]

            await repo.log_event(
                "voice.home_assistant.in", {"user": user.id, "messages": incoming_messages}
            )
            result = await self._collect_response(
                context, user_id=user.id, chat_id=None,
                channel=channel, ctx_extra=ctx_extra
            )
            async for sse in _fake_stream(result.text, result.model):
                yield sse
            asyncio.create_task(
                self._post_turn_jobs_no_chat(
                    user_id=user.id,
                    user_text=last_user_text,
                    assistant_text=result.text,
                )
            )
            await session.commit()
            return

        # ---- PERSISTENT con retrieval híbrido ----
        ctx_extra = ctx_extra or {}
        force_chat_id = ctx_extra.get("force_chat_id")
        external_id = ctx_extra.get("external_id")

        if force_chat_id:
            chat = await repo.get_chat(force_chat_id)
            if not chat:
                raise ValueError(f"force_chat_id {force_chat_id} not found")
            new_user_msg = incoming_messages[-1]
            await repo.add_message(
                chat.id,
                role=new_user_msg["role"],
                content={"text": new_user_msg.get("content", "")},
            )
        elif external_id:
            # whatsapp / external channel: chat eterno por contacto
            chat = await repo.find_chat_by_external_id(user.id, channel, external_id)
            if not chat:
                chat = await repo.create_chat(
                    user.id, channel=channel, mode=mode.value, external_id=external_id
                )
            new_user_msg = incoming_messages[-1]
            await repo.add_message(
                chat.id,
                role=new_user_msg["role"],
                content={"text": new_user_msg.get("content", "")},
            )
        else:
            # web/persistent normal: matching por firma
            prior = incoming_messages[:-1]
            signature_in = hash_messages(prior) if prior else ""
            chat = (
                await repo.find_chat_by_signature(user.id, signature_in)
                if signature_in
                else None
            )
            if not chat:
                chat = await repo.create_chat(user.id, channel=channel, mode=mode.value)
                for m in incoming_messages:
                    await repo.add_message(
                        chat.id, role=m["role"], content={"text": m.get("content", "")}
                    )
            else:
                new_user_msg = incoming_messages[-1]
                await repo.add_message(
                    chat.id,
                    role=new_user_msg["role"],
                    content={"text": new_user_msg.get("content", "")},
                )

        # ---- construir contexto enriquecido ----
        last_user_text = self._extract_text(incoming_messages[-1])

        memories_task = self.memory.retrieve_relevant(session, user.id, last_user_text, k=5)
        chunks_task = self.knowledge.search(session, user.id, last_user_text, k=5)
        summary_task = repo.get_summary(chat.id)

        memories, chunks, summary = await asyncio.gather(
            memories_task, chunks_task, summary_task
        )

        # extract optional channel-specific system prompt (voice_chat, whatsapp, etc.)
        base_system = ""
        if incoming_messages and incoming_messages[0]["role"] == "system":
            base_system = incoming_messages[0].get("content", "")

        system_msg = self._build_system_prompt(memories, chunks, summary, base_system=base_system)

        # filter system messages from raw_msgs to avoid duplication
        window = 10 if summary else RECENT_MESSAGES_WINDOW
        raw_msgs = [m for m in incoming_messages[-window:] if m.get("role") != "system"]
        context = [{"role": "system", "content": system_msg}, *raw_msgs]

        result = await self._collect_response(
            context, user_id=user.id, chat_id=chat.id,
            channel=channel, ctx_extra=ctx_extra
        )
        async for sse in _fake_stream(result.text, result.model):
            yield sse

        # only update signature for web channel (external_id and force_chat_id don't use it)
        if not force_chat_id and not external_id:
            full_history = incoming_messages + [{"role": "assistant", "content": result.text}]
            new_signature = hash_messages(full_history)
            await repo.update_chat_signature(chat, new_signature)

        assistant_row = await repo.add_message(
            chat.id,
            role="assistant",
            content={"text": result.text},
            model=result.model,
            token_usage=result.usage,
            trace_id=result.trace_id,
        )
        await session.commit()

        asyncio.create_task(
            self._post_turn_jobs(
                user_id=user.id,
                chat_id=chat.id,
                user_text=last_user_text,
                assistant_text=result.text,
                assistant_msg_id=assistant_row.id,
            )
        )

    # --- loop interno con tools ---

    @observe(name="tool_loop")
    async def _collect_response(
        self,
        messages: list[dict],
        *,
        user_id: str,
        chat_id: str | None,
        channel: str = "web",
        ctx_extra: dict | None = None,
    ) -> _LoopResult:
        """CR-2: returns result by value — safe for concurrent calls.
        TD-6: channel filters which tools the LLM sees."""
        ctx: dict = {"user_id": user_id, "chat_id": chat_id}
        if ctx_extra:
            ctx.update(ctx_extra)
        working = list(messages)

        for iteration in range(MAX_TOOL_LOOPS):
            req = CompletionRequest(
                messages=working,
                model_hint=pick_model(working),
                tools=openai_tool_specs(channel=channel) or None,
                metadata={"chat_id": chat_id, "user_id": user_id},
            )
            t0 = time.monotonic()
            resp = await self.gw.complete(req)
            llm_ms = int((time.monotonic() - t0) * 1000)
            print(
                f"[timing] llm iter={iteration} model={resp.model_used} "
                f"dt={llm_ms}ms tools={len(resp.tool_calls or [])}"
            )

            if not resp.tool_calls:
                return _LoopResult(
                    text=resp.content,
                    model=resp.model_used,
                    usage=resp.usage,
                    trace_id=resp.trace_id,
                )

            working.append(
                {
                    "role": "assistant",
                    "content": resp.content or None,
                    "tool_calls": resp.tool_calls,
                }
            )

            for tc in resp.tool_calls:
                fn = tc["function"]
                args = json.loads(fn.get("arguments", "{}") or "{}")
                tool = REGISTRY.get(fn["name"])
                t_tool = time.monotonic()
                if not tool:
                    result = {"error": f"unknown tool {fn['name']}"}
                else:
                    try:
                        result = await tool.run(args, ctx)
                    except Exception as e:
                        result = {"error": str(e)}
                tool_ms = int((time.monotonic() - t_tool) * 1000)
                print(f"[timing] tool {fn['name']} dt={tool_ms}ms")
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        return _LoopResult(
            text="[stopped: too many tool iterations]",
            model="n/a",
            usage={},
            trace_id="",
        )

    async def collect_response_streaming(
        self,
        messages: list[dict],
        *,
        user_id: str,
        chat_id: str | None,
        channel: str = "voice",
        ctx_extra: dict | None = None,
    ):
        """Async generator yielding ('prelim', text) | ('final', text) | ('done', None).

        Emits prelim (the filler text the LLM produces before a tool call) as soon as
        iteration 0 returns. Tool execution happens AFTER prelim is emitted so the
        caller can TTS-play prelim while the next LLM iteration runs.
        """
        ctx: dict = {"user_id": user_id, "chat_id": chat_id}
        if ctx_extra:
            ctx.update(ctx_extra)
        working = list(messages)

        for iteration in range(MAX_TOOL_LOOPS):
            req = CompletionRequest(
                messages=working,
                model_hint=pick_model(working),
                tools=openai_tool_specs(channel=channel) or None,
                metadata={"chat_id": chat_id, "user_id": user_id},
            )
            t0 = time.monotonic()
            resp = await self.gw.complete(req)
            llm_ms = int((time.monotonic() - t0) * 1000)
            print(
                f"[timing] llm iter={iteration} model={resp.model_used} "
                f"dt={llm_ms}ms tools={len(resp.tool_calls or [])}"
            )

            if not resp.tool_calls:
                yield ("final", resp.content)
                yield ("done", None)
                return

            if resp.content and resp.content.strip():
                yield ("prelim", resp.content.strip())

            working.append(
                {
                    "role": "assistant",
                    "content": resp.content or None,
                    "tool_calls": resp.tool_calls,
                }
            )

            for tc in resp.tool_calls:
                fn = tc["function"]
                args = json.loads(fn.get("arguments", "{}") or "{}")
                tool = REGISTRY.get(fn["name"])
                t_tool = time.monotonic()
                if not tool:
                    result = {"error": f"unknown tool {fn['name']}"}
                else:
                    try:
                        result = await tool.run(args, ctx)
                    except Exception as e:
                        result = {"error": str(e)}
                tool_ms = int((time.monotonic() - t_tool) * 1000)
                print(f"[timing] tool {fn['name']} dt={tool_ms}ms")
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        yield ("final", "[stopped: too many tool iterations]")
        yield ("done", None)

    # ---- helpers ----

    def _now_block(self) -> str:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Madrid")
        now = datetime.now(tz)
        return (
            "## Current time\n"
            f"{now.strftime('%A %d %B %Y, %H:%M')} ({tz.key}). "
            "Use this directly for time/date questions — do NOT call `time_now`."
        )

    def _extract_text(self, msg: dict) -> str:
        c = msg.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):  # multimodal
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        return ""

    def _build_system_prompt(self, memories, chunks, summary, base_system: str = "") -> str:
        default = "You are a personal AI assistant. Be concise, accurate, and helpful."
        parts = [base_system if base_system else default]
        parts.append(self._now_block())

        if memories:
            mem_lines = "\n".join(f"- ({k}) {c}" for k, c, _ in memories)
            parts.append(
                "## What I currently know about the user\n"
                "These are facts believed to be CURRENTLY TRUE. "
                "If the user contradicts any, treat the new info as canonical.\n"
                f"{mem_lines}"
            )

        if chunks:
            chunk_lines = []
            for title, content, _doc_id, _score in chunks:
                snippet = content[:600].strip()
                chunk_lines.append(f"### From: {title}\n{snippet}")
            parts.append(
                "## Relevant excerpts from the user's knowledge base\n"
                + "\n\n".join(chunk_lines)
            )

        if summary:
            parts.append(f"## Earlier conversation summary\n{summary.summary}")

        return "\n\n".join(parts)

    def _build_voice_system_prompt(self, base_system: str, memories, chunks) -> str:
        parts = [base_system] if base_system else []
        parts.append(self._now_block())
        if memories:
            mem_lines = "\n".join(f"- ({k}) {c}" for k, c, _ in memories)
            parts.append(f"## What I know about the user\n{mem_lines}")
        if chunks:
            chunk_lines = []
            for title, content, _doc_id, _score in chunks:
                chunk_lines.append(f"### From {title}\n{content[:400]}")
            parts.append(
                "## Relevant from knowledge base\n" + "\n\n".join(chunk_lines)
            )
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
        """TD-10: structured event logging for background jobs."""
        try:
            async with SessionLocal() as s:
                stats = await self.extractor.extract_and_store(
                    s,
                    user_id=user_id,
                    chat_id=chat_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    source_message_id=assistant_msg_id,
                )
                await Repository(s).log_event("memory.extracted", stats)
                await s.commit()
            async with SessionLocal() as s:
                summarized = await self.summarizer.maybe_summarize(s, chat_id=chat_id)
                await Repository(s).log_event(
                    "summary.attempted", {"summarized": summarized, "chat_id": chat_id}
                )
                await s.commit()
        except Exception as e:
            try:
                async with SessionLocal() as s:
                    await Repository(s).log_event(
                        "post_turn_jobs.error",
                        {"error": str(e), "chat_id": chat_id, "user_id": user_id},
                    )
                    await s.commit()
            except Exception:
                pass  # can't log the error about logging
            print(f"[post_turn_jobs] error: {e}")

    async def _post_turn_jobs_no_chat(
        self, *, user_id: str, user_text: str, assistant_text: str
    ):
        """Como _post_turn_jobs pero sin chat (home_assistant turns no crean chat)."""
        try:
            async with SessionLocal() as s:
                stats = await self.extractor.extract_and_store(
                    s,
                    user_id=user_id,
                    chat_id=None,
                    source_message_id=None,
                    user_text=user_text,
                    assistant_text=assistant_text,
                )
                await Repository(s).log_event("memory.extracted", {**stats, "source": "voice"})
                await s.commit()
        except Exception as e:
            try:
                async with SessionLocal() as s:
                    await Repository(s).log_event(
                        "post_turn_jobs.error",
                        {"error": str(e), "user_id": user_id, "source": "voice"},
                    )
                    await s.commit()
            except Exception:
                pass
            print(f"[post_turn_jobs_no_chat] {e}")


async def _fake_stream(text: str, model: str, words_per_chunk: int = 4):
    """TD-8: Word-based SSE chunks so words aren't split mid-stream."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    words = text.split(" ")
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i : i + words_per_chunk])
        if i + words_per_chunk < len(words):
            chunk += " "
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
        await asyncio.sleep(0.04)
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"
