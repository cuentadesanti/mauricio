from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import ToolSpec


class MemoryListTool:
    spec = ToolSpec(
        name="memory_list",
        description=(
            "List everything I currently remember about the user. "
            "Call this when the user asks 'what do you know about me?', "
            "'what do you remember?', or similar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["fact", "preference", "entity", "all"],
                    "description": "Filter by memory kind, or 'all' for everything.",
                    "default": "all",
                }
            },
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        user_id = ctx.get("user_id")
        if not user_id:
            return {"error": "no user context"}
        kind = args.get("kind", "all")
        kinds = [kind] if kind != "all" else None

        async with SessionLocal() as session:
            repo = Repository(session)
            memories = await repo.list_active_memories(user_id, kinds=kinds)

        items = [
            {
                "kind": m.kind,
                "content": m.content,
                "since": m.valid_from.isoformat() if m.valid_from else None,
            }
            for m in memories[:50]  # cap to avoid huge payloads
        ]
        return {"count": len(items), "memories": items}
