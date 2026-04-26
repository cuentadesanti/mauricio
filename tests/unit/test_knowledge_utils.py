import pytest
from backend.services.knowledge_service import chunk_text, parse_frontmatter


def test_chunk_text_simple():
    text = "line1\nline2\nline3"
    # size 10, overlap 2
    chunks = chunk_text(text, size=10, overlap=2)
    assert len(chunks) > 1
    assert "line1" in chunks[0]


def test_chunk_text_cuts_at_newline():
    # Longitud de "1234567890123456\n" es 17.
    # Con size 30, el newline está en el índice 16.
    # 16 > 30 // 2 (15) -> debería cortar ahí.
    text = "1234567890123456\n" + "x" * 50
    chunks = chunk_text(text, size=30, overlap=5)
    assert chunks[0] == "1234567890123456"


def test_parse_frontmatter_valid():
    content = "---\ntitle: My Note\ntags: test, note\n---\nBody here"
    meta, body = parse_frontmatter(content)
    assert meta == {"title": "My Note", "tags": "test, note"}
    assert body == "Body here"


def test_parse_frontmatter_none():
    content = "Just body"
    meta, body = parse_frontmatter(content)
    assert meta == {}
    assert body == "Just body"
