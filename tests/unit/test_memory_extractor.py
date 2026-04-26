import json
from unittest.mock import AsyncMock, patch

import pytest
from backend.services.memory_extractor import MemoryExtractor


@pytest.mark.asyncio
async def test_memory_extractor_success():
    mock_gw = AsyncMock()
    mock_mem = AsyncMock()
    session = AsyncMock()

    # Simular respuesta del LLM con JSON
    mock_resp = AsyncMock()
    mock_resp.content = json.dumps(
        {"facts": ["User likes pizza"], "preferences": [], "entities": []}
    )
    mock_gw.complete.return_value = mock_resp

    # Simular guardado exitoso
    mock_mem.store_unique.return_value = "m1"

    extractor = MemoryExtractor(gw=mock_gw, mem=mock_mem)
    result = await extractor.extract_and_store(
        session,
        user_id="u1",
        chat_id="c1",
        user_text="I love pizza",
        assistant_text="Noted.",
    )

    assert result["stored"] == 1
    mock_mem.store_unique.assert_called_with(
        session,
        user_id="u1",
        kind="fact",
        content="User likes pizza",
        source_chat_id="c1",
        source_message_id=None,
    )
