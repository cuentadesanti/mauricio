"""Carga prompts externos desde prompts/*.md.

El directorio prompts/ vive en la raíz del repositorio y es editable
por el bot de self-improvement sin tocar código Python (Loop A).
"""

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Carga prompts/{name}.md. Cacheado en memoria; reinicia con el proceso."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")
