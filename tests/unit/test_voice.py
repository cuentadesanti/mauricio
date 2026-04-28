"""Unit tests for VoiceOrchestrator and voice tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from backend.services.voice_orchestrator import (
    VoiceOrchestrator,
    _extract_text_from_sse,
)

# ---- SSE text extraction ----


def test_extract_text_from_sse_valid():
    import json

    payload = {
        "choices": [{"index": 0, "delta": {"content": "hello"}}]
    }
    sse = f"data: {json.dumps(payload)}\n\n"
    assert _extract_text_from_sse(sse) == "hello"


def test_extract_text_from_sse_done():
    assert _extract_text_from_sse("data: [DONE]\n\n") == ""


def test_extract_text_from_sse_no_content():
    import json

    payload = {"choices": [{"index": 0, "delta": {}}]}
    sse = f"data: {json.dumps(payload)}\n\n"
    assert _extract_text_from_sse(sse) == ""


def test_extract_text_from_sse_garbage():
    assert _extract_text_from_sse("not an sse line") == ""


# ---- VoiceOrchestrator ----


@pytest.mark.asyncio
async def test_orchestrator_empty_transcript():
    mock_chat = AsyncMock()
    orch = VoiceOrchestrator(chat_service=mock_chat)
    result = await orch.handle_transcript(satellite_id="test", transcript="  ")
    assert result == ""


@pytest.mark.asyncio
async def test_orchestrator_home_assistant_mode():
    """Verifies that home_assistant mode calls ChatService.handle correctly."""
    import json

    mock_chat = AsyncMock()

    # Mock the SSE chunks that ChatService.handle yields
    async def fake_handle(*args, **kwargs):
        payload = {"choices": [{"index": 0, "delta": {"content": "Hola"}}]}
        yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"

    mock_chat.handle = fake_handle

    # Mock the SessionLocal context and Repository
    mock_session = AsyncMock()
    mock_repo = AsyncMock()
    mock_user = MagicMock(id="u1")
    mock_sat = MagicMock(mode="home_assistant", active_chat_id=None, mode_until=None)
    mock_repo.get_or_create_user.return_value = mock_user
    mock_repo.get_or_create_satellite.return_value = mock_sat

    with (
        patch(
            "backend.services.voice_orchestrator.SessionLocal",
            return_value=mock_session,
        ),
        patch(
            "backend.services.voice_orchestrator.Repository",
            return_value=mock_repo,
        ),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        orch = VoiceOrchestrator(chat_service=mock_chat)
        result = await orch.handle_transcript(
            satellite_id="test-sat", transcript="qué hora es"
        )

    assert result == "Hola"


# ---- Voice tools ----


@pytest.mark.asyncio
async def test_start_voice_chat_no_satellite():
    from backend.tools.start_voice_chat import StartVoiceChatTool

    tool = StartVoiceChatTool()
    result = await tool.run({}, {"user_id": "u1"})
    assert result["ok"] is False
    assert "not a voice context" in result["error"]


@pytest.mark.asyncio
async def test_end_voice_chat_no_satellite():
    from backend.tools.end_voice_chat import EndVoiceChatTool

    tool = EndVoiceChatTool()
    result = await tool.run({}, {"user_id": "u1"})
    assert result["ok"] is False
    assert "not a voice context" in result["error"]


@pytest.mark.asyncio
async def test_start_voice_chat_with_satellite():
    from backend.tools.start_voice_chat import StartVoiceChatTool

    tool = StartVoiceChatTool()

    mock_session = AsyncMock()
    mock_repo = AsyncMock()
    mock_user = MagicMock(id="u1")
    mock_chat = MagicMock(id="chat-123")
    mock_repo.get_or_create_user.return_value = mock_user
    mock_repo.create_chat.return_value = mock_chat

    with (
        patch(
            "backend.tools.start_voice_chat.SessionLocal",
            return_value=mock_session,
        ),
        patch(
            "backend.tools.start_voice_chat.Repository",
            return_value=mock_repo,
        ),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await tool.run(
            {"intro": "Sure!"}, {"user_id": "u1", "satellite_id": "living-room"}
        )

    assert result["ok"] is True
    assert result["chat_id"] == "chat-123"
    assert result["say"] == "Sure!"
    mock_repo.update_satellite_mode.assert_called_once()


@pytest.mark.asyncio
async def test_end_voice_chat_with_satellite():
    from backend.tools.end_voice_chat import EndVoiceChatTool

    tool = EndVoiceChatTool()

    mock_session = AsyncMock()
    mock_repo = AsyncMock()

    with (
        patch(
            "backend.tools.end_voice_chat.SessionLocal",
            return_value=mock_session,
        ),
        patch(
            "backend.tools.end_voice_chat.Repository",
            return_value=mock_repo,
        ),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await tool.run(
            {"farewell": "Bye!"}, {"user_id": "u1", "satellite_id": "living-room"}
        )

    assert result["ok"] is True
    assert result["say"] == "Bye!"
    mock_repo.update_satellite_mode.assert_called_once_with(
        "living-room", "home_assistant", None, None
    )
