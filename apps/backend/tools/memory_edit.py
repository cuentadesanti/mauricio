from ..db.repository import Repository
from ..db.session import SessionLocal
from ..gateways.embeddings_gateway import EmbeddingsGateway
from ..services.memory_service import MemoryService
from .base import ToolSpec


class MemoryEditTool:
    spec = ToolSpec(
        name="memory_edit",
        description=(
            "Modify the user's long-term memories. Use when the user explicitly asks "
            "to forget, correct, or update something they previously told you.\n"
            "Three operations:\n"
            " - 'expire': mark a memory as no longer true (no replacement)\n"
            " - 'correct': replace one or more memories with a new one\n"
            " - 'add': add a new memory directly without inference"
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["expire", "correct", "add"]},
                "search_query": {
                    "type": "string",
                    "description": (
                        "For expire/correct: description of what to find and modify "
                        "(semantic search). Required if memory_ids not given."
                    ),
                },
                "memory_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific memory ids if known.",
                },
                "new_content": {
                    "type": "string",
                    "description": (
                        "Required for correct/add. "
                        "New memory text in third person ('the user ...')."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "preference", "entity", "note"],
                    "description": (
                        "Required for add. "
                        "For correct, defaults to the kind of the replaced memory."
                    ),
                },
            },
            "required": ["operation"],
        },
    )

    def __init__(self):
        self.emb = EmbeddingsGateway()
        self.mem = MemoryService(self.emb)

    async def run(self, args: dict, ctx: dict) -> dict:
        op = args["operation"]
        user_id = ctx["user_id"]
        chat_id = ctx.get("chat_id")

        async with SessionLocal() as session:
            repo = Repository(session)

            target_ids = list(args.get("memory_ids") or [])
            if args.get("search_query") and not target_ids:
                emb = await self.emb.embed_one(args["search_query"])
                results = await repo.search_memories(
                    user_id, emb, k=5, min_score=0.5, active_only=True
                )
                target_ids = [m.id for m, _ in results[:3]]

            if op == "expire":
                if not target_ids:
                    return {"ok": False, "error": "no memories matched"}
                expired = []
                for mid in target_ids:
                    m = await repo.get_memory(mid)
                    if m and m.valid_until is None:
                        await repo.expire_memory(mid)
                        expired.append({"id": mid, "content": m.content})
                await session.commit()
                return {"ok": True, "operation": "expire", "expired": expired}

            if op == "correct":
                if not args.get("new_content"):
                    return {"ok": False, "error": "new_content required"}
                if not target_ids:
                    return {"ok": False, "error": "no memories matched"}
                first = await repo.get_memory(target_ids[0])
                kind = args.get("kind") or (first.kind if first else "fact")
                new_id = await self.mem.store_unique(
                    session,
                    user_id=user_id,
                    kind=kind,
                    content=args["new_content"],
                    source_chat_id=chat_id,
                )
                if not new_id:
                    emb = await self.emb.embed_one(args["new_content"])
                    existing = await repo.find_similar_memory(user_id, kind, emb, threshold=0.92)
                    new_id = existing.id if existing else None
                if not new_id:
                    return {"ok": False, "error": "failed to create replacement"}
                replaced = []
                for mid in target_ids:
                    m = await repo.get_memory(mid)
                    if m and m.valid_until is None and mid != new_id:
                        await repo.expire_memory(mid, replaced_by=new_id)
                        replaced.append({"id": mid, "content": m.content})
                await session.commit()
                return {"ok": True, "operation": "correct", "new_id": new_id, "replaced": replaced}

            if op == "add":
                kind = args.get("kind") or "fact"
                content = args.get("new_content")
                if not content:
                    return {"ok": False, "error": "new_content required"}
                new_id = await self.mem.store_unique(
                    session,
                    user_id=user_id,
                    kind=kind,
                    content=content,
                    source_chat_id=chat_id,
                )
                await session.commit()
                if new_id:
                    return {"ok": True, "operation": "add", "id": new_id}
                return {"ok": True, "operation": "add", "skipped": "duplicate"}

            return {"ok": False, "error": f"unknown operation {op}"}
