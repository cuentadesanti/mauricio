from tavily import AsyncTavilyClient

from ..core.config import settings
from .base import ToolSpec


class WebSearchTool:
    spec = ToolSpec(
        name="web_search",
        description=(
            "Search the web for current information. Use for questions about news, "
            "current events, recent data, or anything that may have changed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
    )

    def __init__(self):
        if not settings.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY missing")
        self.client = AsyncTavilyClient(api_key=settings.tavily_api_key)

    async def run(self, args: dict, ctx: dict) -> dict:
        query = args["query"]
        max_results = args.get("max_results", 5)
        result = await self.client.search(query=query, max_results=max_results, include_answer=True)
        return {
            "answer": result.get("answer"),
            "results": [
                {"title": r["title"], "url": r["url"], "content": r["content"][:500]}
                for r in result.get("results", [])
            ],
        }
