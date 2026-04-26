from datetime import datetime
from pathlib import Path

from .base import ToolSpec

KNOWLEDGE_DIR = Path("/app/knowledge")  # montado desde el repo


class NoteAddTool:
    spec = ToolSpec(
        name="note_add",
        description=(
            "Save a note to the user's personal knowledge base. Use when the user asks you to "
            "remember, save, write down, or note something. The note is stored as markdown."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the note."},
                "content": {"type": "string", "description": "Markdown content of the note."},
                "tags": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["title", "content"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        title = args["title"]
        content = args["content"]
        tags = args.get("tags", [])

        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:60]
        ts = datetime.utcnow().strftime("%Y-%m-%d-%H%M%S")
        filename = f"{ts}-{slug}.md"

        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        path = KNOWLEDGE_DIR / filename

        frontmatter = "---\n"
        frontmatter += f"title: {title}\n"
        frontmatter += f"created: {datetime.utcnow().isoformat()}\n"
        if tags:
            frontmatter += f"tags: [{', '.join(tags)}]\n"
        frontmatter += "---\n\n"

        path.write_text(frontmatter + content, encoding="utf-8")
        return {"saved": True, "path": str(path.relative_to(KNOWLEDGE_DIR.parent))}
