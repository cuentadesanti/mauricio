from unittest.mock import AsyncMock, patch

import pytest
from backend.services.memory_service import MemoryService


@pytest.mark.asyncio
async def test_memory_service_store_unique_skips_if_similar():
    mock_emb = AsyncMock()
    mock_repo = AsyncMock()

    # simula que encuentra algo similar
    mock_repo.find_similar_memory.return_value = AsyncMock(id="existing_id")

    with patch("backend.services.memory_service.Repository", return_value=mock_repo):
        service = MemoryService(embeddings=mock_emb)
        session = AsyncMock()

        result = await service.store_unique(
            session, user_id="u1", kind="fact", content="test content"
        )

        assert result is None
        mock_repo.insert_memory.assert_not_called()


@pytest.mark.asyncio
async def test_memory_service_store_unique_inserts_if_new():
    mock_emb = AsyncMock()
    mock_repo = AsyncMock()

    # simula que NO encuentra nada similar
    mock_repo.find_similar_memory.return_value = None
    mock_repo.insert_memory.return_value = AsyncMock(id="new_id")

    with patch("backend.services.memory_service.Repository", return_value=mock_repo):
        service = MemoryService(embeddings=mock_emb)
        session = AsyncMock()

        result = await service.store_unique(
            session, user_id="u1", kind="fact", content="new content"
        )

        assert result == "new_id"
        mock_repo.insert_memory.assert_called_once()
