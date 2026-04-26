import json
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.memory_extractor import MemoryExtractor


async def test_memory_extractor_stores_facts():
    mock_gw = AsyncMock()
    mock_mem = AsyncMock()
    mock_mem.store_unique.return_value = "m1"

    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "facts": [{"content": "The user likes pizza", "valid_from": None, "supersedes": []}],
        "preferences": [],
        "entities": [],
        "expire": [],
    })
    mock_gw.complete.return_value = mock_resp

    mock_repo = AsyncMock()
    mock_repo.list_active_memories.return_value = []

    extractor = MemoryExtractor(gw=mock_gw, mem=mock_mem)
    with patch("backend.services.memory_extractor.Repository", return_value=mock_repo):
        result = await extractor.extract_and_store(
            AsyncMock(),
            user_id="u1",
            chat_id="c1",
            user_text="I love pizza",
            assistant_text="Noted.",
        )

    assert result["stored"] == 1
    assert result["skipped"] == 0
    assert result["expired"] == 0
    mock_mem.store_unique.assert_called_once()
    call_kwargs = mock_mem.store_unique.call_args.kwargs
    assert call_kwargs["kind"] == "fact"
    assert call_kwargs["content"] == "The user likes pizza"
    assert call_kwargs["valid_from"] is None


async def test_memory_extractor_handles_supersession():
    mock_gw = AsyncMock()
    mock_mem = AsyncMock()
    mock_mem.store_unique.return_value = "m2"

    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "facts": [{"content": "The user lives in Lisbon", "valid_from": None, "supersedes": ["m1"]}],
        "preferences": [],
        "entities": [],
        "expire": [],
    })
    mock_gw.complete.return_value = mock_resp

    mock_repo = AsyncMock()
    mock_repo.list_active_memories.return_value = [
        MagicMock(id="m1", kind="fact", content="The user lives in Madrid"),
    ]

    extractor = MemoryExtractor(gw=mock_gw, mem=mock_mem)
    with patch("backend.services.memory_extractor.Repository", return_value=mock_repo):
        result = await extractor.extract_and_store(
            AsyncMock(),
            user_id="u1",
            chat_id="c1",
            user_text="I moved to Lisbon",
            assistant_text="Got it.",
        )

    assert result["stored"] == 1
    assert result["expired"] == 1
    mock_repo.expire_memory.assert_called_once_with("m1", replaced_by="m2")


async def test_memory_extractor_explicit_expire():
    mock_gw = AsyncMock()
    mock_mem = AsyncMock()

    mock_resp = MagicMock()
    mock_resp.content = json.dumps({
        "facts": [],
        "preferences": [],
        "entities": [],
        "expire": ["m_old"],
    })
    mock_gw.complete.return_value = mock_resp

    mock_repo = AsyncMock()
    mock_repo.list_active_memories.return_value = []

    extractor = MemoryExtractor(gw=mock_gw, mem=mock_mem)
    with patch("backend.services.memory_extractor.Repository", return_value=mock_repo):
        result = await extractor.extract_and_store(
            AsyncMock(),
            user_id="u1",
            chat_id="c1",
            user_text="I no longer have a dog",
            assistant_text="Understood.",
        )

    assert result["expired"] == 1
    mock_repo.expire_memory.assert_called_once_with("m_old", replaced_by=None)


async def test_memory_extractor_bad_json_returns_error():
    mock_gw = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.content = "not json at all"
    mock_gw.complete.return_value = mock_resp

    mock_repo = AsyncMock()
    mock_repo.list_active_memories.return_value = []

    extractor = MemoryExtractor(gw=mock_gw, mem=AsyncMock())
    with patch("backend.services.memory_extractor.Repository", return_value=mock_repo):
        result = await extractor.extract_and_store(
            AsyncMock(),
            user_id="u1",
            chat_id="c1",
            user_text="hi",
            assistant_text="hi",
        )

    assert result.get("error") == "bad_json"
    assert result["stored"] == 0
