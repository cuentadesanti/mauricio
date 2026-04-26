from datetime import UTC, datetime
from pathlib import Path

from .base import ToolSpec

KNOWLEDGE_DIR = Path("/app/knowledge")  # montado desde el repo


def _write_note(path: Path, title: str, content: str, tags: list[str], now: datetime) -> None:
    frontmatter = "---\n"
    frontmatter += f"title: {title}\n"
    frontmatter += f"created: {now.isoformat()}\n"
    if tags:
        frontmatter += f"tags: [{', '.join(tags)}]\n"
    frontmatter += "---\n\n"
    path.write_text(frontmatter + content, encoding="utf-8")


def _find_existing(title: str) -> Path | None:
    """Busca una nota con el mismo título (case-insensitive)."""
    if not KNOWLEDGE_DIR.exists():
        return None
    title_lower = title.lower().strip()
    for path in KNOWLEDGE_DIR.glob("*.md"):
        if path.name.startswith("."):
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("title:") and line[6:].strip().lower() == title_lower:
                return path
    return None


class NoteAddTool:
    spec = ToolSpec(
        name="note_add",
        description=(
            "Save or update a note in the user's personal knowledge base. "
            "If a note with the same title already exists it is updated in place "
            "(no duplicate is created). Use when the user asks to remember, save, "
            "write down, or update something. Content is stored as markdown."
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

        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)

        existing = _find_existing(title)
        if existing:
            # Merge tags from existing note
            old_text = existing.read_text(encoding="utf-8")
            for line in old_text.splitlines():
                if line.startswith("tags:"):
                    old_tags = [t.strip(" []") for t in line[5:].split(",") if t.strip(" []")]
                    tags = list(dict.fromkeys(old_tags + tags))  # deduplicate, preserve order
                    break
            _write_note(existing, title, content, tags, now)
            rel = str(existing.relative_to(KNOWLEDGE_DIR.parent))
            return {"saved": True, "updated": True, "path": rel}

        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())[:60]
        ts = now.strftime("%Y-%m-%d-%H%M%S")
        path = KNOWLEDGE_DIR / f"{ts}-{slug}.md"
        _write_note(path, title, content, tags, now)
        return {"saved": True, "updated": False, "path": str(path.relative_to(KNOWLEDGE_DIR.parent))}  # noqa: E501
