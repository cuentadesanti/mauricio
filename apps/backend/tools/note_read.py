from .base import ToolSpec
from .note_add import KNOWLEDGE_DIR
from .note_list import parse_note


class NoteReadTool:
    spec = ToolSpec(
        name="note_read",
        description=(
            "Read the full content of a note from the user's knowledge base. "
            "Pass the exact filename returned by note_list."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Exact filename returned by note_list.",
                }
            },
            "required": ["filename"],
        },
    )

    async def run(self, args: dict, ctx: dict) -> dict:
        path = KNOWLEDGE_DIR / args["filename"]
        if not path.exists():
            return {"error": f"Note '{args['filename']}' not found."}
        n = parse_note(path)
        return {
            "filename": n["filename"],
            "title": n["title"],
            "created": n["created"],
            "tags": n["tags"],
            "content": n["body"],
        }
