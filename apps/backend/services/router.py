import re

from ..core.config import settings

_STRONG_KEYWORDS = re.compile(
    r"\b("
    r"código|debug|stack ?trace|exception|"
    r"derivada|integral|teorema|demuestra|"
    r"plan|estrategia|arquitectura|"
    r"compara|analiza|"
    r"code|prove|theorem|architecture|"
    r"compare|analyze"
    r")\b",
    re.IGNORECASE,
)


def pick_model(messages: list[dict]) -> str:
    """Reglas v0:
    - Strong si la query parece compleja (keywords o muy larga)
    - Strong si hay muchísimo contexto
    - Default = haiku
    """
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not last_user:
        return settings.default_model

    text = last_user.get("content", "")
    if isinstance(text, list):  # multimodal
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))

    if _STRONG_KEYWORDS.search(text):
        return settings.strong_model

    if len(text) > 800:
        return settings.strong_model

    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    if total_chars > 8000:
        return settings.strong_model

    return settings.default_model
