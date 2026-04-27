from ..db.repository import Repository
from ..db.session import SessionLocal
from .base import ToolSpec


class ChatSearchTool:
    spec = ToolSpec(
        name="chat_search",
        description=(
            "Search through past conversations with the user. "
            "Call this when the user asks 'do you remember when we talked about X?', "
            "'what did I say about Y?', or similar. Returns matching messages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in past messages.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        user_id = ctx.get("user_id")
        if not user_id:
            return {"error": "no user context"}
        query = args["query"]
        limit = min(args.get("max_results", 5), 10)

        async with SessionLocal() as session:
            repo = Repository(session)
            results = await repo.search_messages(user_id, query, limit=limit)

        hits = [
            {
                "role": msg.role,
                "text": (msg.content or {}).get("text", "")[:300],
                "chat_id": chat_id,
                "date": msg.created_at.isoformat(),
            }
            for msg, chat_id in results
        ]
        return {"count": len(hits), "matches": hits}
