import re
from pathlib import Path

from .base import ToolSpec
from .note_add import KNOWLEDGE_DIR

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)", re.DOTALL)


def parse_note(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {"filename": path.name, "title": path.stem, "created": "", "tags": [], "body": text}
    meta_block, body = m.group(1), m.group(2).strip()
    meta: dict = {}
    for line in meta_block.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            meta[k.strip()] = v.strip()
    tags_raw = meta.get("tags", "")
    tags = [t.strip(" []") for t in tags_raw.split(",") if t.strip(" []")]
    return {
        "filename": path.name,
        "title": meta.get("title", path.stem),
        "created": meta.get("created", ""),
        "tags": tags,
        "body": body,
    }


class NoteListTool:
    spec = ToolSpec(
        name="note_list",
        description=(
            "List all notes in the user's personal knowledge base. "
            "Returns title, date, tags, and a short snippet of each note. "
            "Use this before note_add to check for existing notes on the same topic."
        ),
        parameters={"type": "object", "properties": {}},
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        if not KNOWLEDGE_DIR.exists():
            return {"notes": []}
        notes = []
        for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
            if path.name.startswith("."):
                continue
            n = parse_note(path)
            notes.append(
                {
                    "filename": n["filename"],
                    "title": n["title"],
                    "created": n["created"],
                    "tags": n["tags"],
                    "snippet": n["body"][:200],
                }
            )
        return {"notes": notes}
