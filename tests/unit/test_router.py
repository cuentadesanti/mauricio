from backend.core.config import settings
from backend.services.router import pick_model


def test_short_simple_uses_default():
    msgs = [{"role": "user", "content": "hola"}]
    assert pick_model(msgs) == settings.default_model


def test_long_query_uses_strong():
    msgs = [{"role": "user", "content": "x" * 1000}]
    assert pick_model(msgs) == settings.strong_model


def test_keyword_uses_strong():
    msgs = [{"role": "user", "content": "compara estas dos arquitecturas"}]
    assert pick_model(msgs) == settings.strong_model
