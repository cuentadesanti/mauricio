from unittest.mock import AsyncMock, patch

import pytest
from backend.services.summarizer import Summarizer


@pytest.mark.asyncio
async def test_summarizer_skips_if_short():
    mock_gw = AsyncMock()
    mock_repo = AsyncMock()

    # Menos de 20 mensajes
    mock_repo.get_messages.return_value = [AsyncMock() for _ in range(5)]

    with patch("backend.services.summarizer.Repository", return_value=mock_repo):
        summarizer = Summarizer(gw=mock_gw)
        session = AsyncMock()

        result = await summarizer.maybe_summarize(session, chat_id="c1")

        assert result is False
        mock_repo.upsert_summary.assert_not_called()


@pytest.mark.asyncio
async def test_summarizer_triggers_if_long():
    mock_gw = AsyncMock()
    mock_repo = AsyncMock()

    # 25 mensajes, no hay summary previo
    msgs = [AsyncMock(id=f"m{i}", role="user", content={"text": "hi"}) for i in range(25)]
    mock_repo.get_messages.return_value = msgs
    mock_repo.get_summary.return_value = None

    # Simular respuesta del LLM
    mock_resp = AsyncMock()
    mock_resp.content = "This is a summary"
    mock_gw.complete.return_value = mock_resp

    with patch("backend.services.summarizer.Repository", return_value=mock_repo):
        summarizer = Summarizer(gw=mock_gw)
        session = AsyncMock()

        result = await summarizer.maybe_summarize(session, chat_id="c1")

        assert result is True
        # Debe resumir hasta el mensaje index cutoff-1 (25 - 10 = 15 -> index 14)
        mock_repo.upsert_summary.assert_called_once()
        args, kwargs = mock_repo.upsert_summary.call_args
        assert kwargs["summary"] == "This is a summary"
        assert kwargs["up_to_message_id"] == "m14"
