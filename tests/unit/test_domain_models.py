from backend.domain.knowledge import Chunk, Document
from backend.domain.memory import Memory


def test_memory_model():
    mem = Memory(user_id="u1", kind="fact", content="Lives in Madrid")
    assert mem.user_id == "u1"
    assert mem.kind == "fact"
    assert mem.content == "Lives in Madrid"
    assert mem.metadata == {}


def test_knowledge_models():
    doc = Document(user_id="u1", s3_key="notes/test.md", content="test", content_hash="hash")
    assert doc.user_id == "u1"
    assert doc.content == "test"

    chunk = Chunk(doc_id="d1", chunk_index=0, content="chunk content")
    assert chunk.doc_id == "d1"
    assert chunk.chunk_index == 0
    assert chunk.score is None
