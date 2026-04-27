import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


async def test_time_now_returns_expected_keys():
    from backend.tools.time_now import TimeNowTool

    tool = TimeNowTool()
    result = await tool.run({"timezone": "Europe/Madrid"}, ctx={})
    assert {"iso", "human", "timezone"} == result.keys()
    assert result["timezone"] == "Europe/Madrid"


async def test_time_now_default_timezone():
    from backend.tools.time_now import TimeNowTool

    tool = TimeNowTool()
    result = await tool.run({}, ctx={})
    assert result["timezone"] == "Europe/Madrid"


async def test_time_now_respects_tz_arg():
    from backend.tools.time_now import TimeNowTool

    tool = TimeNowTool()
    result = await tool.run({"timezone": "America/Mexico_City"}, ctx={})
    assert result["timezone"] == "America/Mexico_City"
    assert "-06:00" in result["iso"] or "-05:00" in result["iso"]


async def test_note_add_creates_file(tmp_path):
    from backend.tools.note_add import NoteAddTool

    with patch("backend.tools.note_add.KNOWLEDGE_DIR", tmp_path):
        tool = NoteAddTool()
        result = await tool.run(
            {"title": "My Test Note", "content": "This is a test."},
            ctx={},
        )

    assert result["saved"] is True
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "My Test Note" in text
    assert "This is a test." in text


async def test_note_add_includes_tags(tmp_path):
    from backend.tools.note_add import NoteAddTool

    with patch("backend.tools.note_add.KNOWLEDGE_DIR", tmp_path):
        tool = NoteAddTool()
        await tool.run(
            {"title": "Tagged Note", "content": "Body", "tags": ["work", "urgent"]},
            ctx={},
        )

    text = list(tmp_path.glob("*.md"))[0].read_text()
    assert "work" in text
    assert "urgent" in text


async def test_note_add_sanitizes_title_in_filename(tmp_path):
    from backend.tools.note_add import NoteAddTool

    with patch("backend.tools.note_add.KNOWLEDGE_DIR", tmp_path):
        tool = NoteAddTool()
        await tool.run({"title": "Hello World! ¿Cómo?", "content": "test"}, ctx={})

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    assert " " not in files[0].name
    assert "!" not in files[0].name


async def test_web_search_returns_formatted_results():
    mock_response = {
        "answer": "Python is a language.",
        "results": [
            {"title": "Python.org", "url": "https://python.org", "content": "docs" * 200},
        ],
    }
    with (
        patch("backend.tools.web_search.settings") as mock_settings,
        patch("backend.tools.web_search.AsyncTavilyClient") as MockClient,
    ):
        mock_settings.tavily_api_key = "test-key"
        mock_client = AsyncMock()
        mock_client.search.return_value = mock_response
        MockClient.return_value = mock_client

        from backend.tools.web_search import WebSearchTool

        tool = WebSearchTool()
        result = await tool.run({"query": "Python", "max_results": 3}, ctx={})

    assert result["answer"] == "Python is a language."
    assert len(result["results"]) == 1
    assert result["results"][0]["url"] == "https://python.org"
    mock_client.search.assert_called_once_with(query="Python", max_results=3, include_answer=True)


async def test_web_search_truncates_content_to_500():
    mock_response = {
        "answer": None,
        "results": [{"title": "T", "url": "https://x.com", "content": "x" * 1000}],
    }
    with (
        patch("backend.tools.web_search.settings") as mock_settings,
        patch("backend.tools.web_search.AsyncTavilyClient") as MockClient,
    ):
        mock_settings.tavily_api_key = "test-key"
        mock_client = AsyncMock()
        mock_client.search.return_value = mock_response
        MockClient.return_value = mock_client

        from backend.tools.web_search import WebSearchTool

        tool = WebSearchTool()
        result = await tool.run({"query": "test"}, ctx={})

    assert len(result["results"][0]["content"]) == 500


async def test_web_search_missing_key_raises():
    with patch("backend.tools.web_search.settings") as mock_settings:
        mock_settings.tavily_api_key = None

        from backend.tools.web_search import WebSearchTool

        try:
            WebSearchTool()
            raise AssertionError("Should have raised RuntimeError")
        except RuntimeError as e:
            assert "TAVILY_API_KEY" in str(e)


def test_tool_specs_filter_voice_only_from_web():
    from backend.tools.registry import openai_tool_specs

    web_specs = openai_tool_specs(channel="web")
    voice_specs = openai_tool_specs(channel="voice")

    web_names = {s["function"]["name"] for s in web_specs}
    voice_names = {s["function"]["name"] for s in voice_specs}

    # voice tools should NOT appear in web
    assert "start_voice_chat" not in web_names
    assert "end_voice_chat" not in web_names

    # voice tools SHOULD appear in voice
    assert "start_voice_chat" in voice_names
    assert "end_voice_chat" in voice_names

    # universal tools appear in both
    assert "time_now" in web_names
    assert "time_now" in voice_names


def test_tool_specs_default_channel_excludes_voice():
    from backend.tools.registry import openai_tool_specs

    default_specs = openai_tool_specs()  # channel="any"
    default_names = {s["function"]["name"] for s in default_specs}

    # "any" channel should still see universal tools
    assert "time_now" in default_names
    # voice-only tools should NOT match "any" (they require "voice")
    assert "start_voice_chat" not in default_names


async def test_memory_list_no_user_returns_error():
    from backend.tools.memory_list import MemoryListTool

    tool = MemoryListTool()
    result = await tool.run({}, ctx={})
    assert "error" in result


async def test_memory_list_returns_memories():
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from backend.tools.memory_list import MemoryListTool

    tool = MemoryListTool()

    mock_session = AsyncMock()
    mock_repo = AsyncMock()
    mock_mem = MagicMock(
        kind="fact", content="user lives in Madrid",
        valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    mock_repo.list_active_memories.return_value = [mock_mem]

    with (
        patch("backend.tools.memory_list.SessionLocal", return_value=mock_session),
        patch("backend.tools.memory_list.Repository", return_value=mock_repo),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await tool.run({"kind": "fact"}, ctx={"user_id": "u1"})

    assert result["count"] == 1
    assert result["memories"][0]["content"] == "user lives in Madrid"
    mock_repo.list_active_memories.assert_called_once_with("u1", kinds=["fact"])


async def test_chat_search_no_user_returns_error():
    from backend.tools.chat_search import ChatSearchTool

    tool = ChatSearchTool()
    result = await tool.run({"query": "test"}, ctx={})
    assert "error" in result


async def test_chat_search_returns_matches():
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from backend.tools.chat_search import ChatSearchTool

    tool = ChatSearchTool()

    mock_session = AsyncMock()
    mock_repo = AsyncMock()
    mock_msg = MagicMock(
        role="user",
        content={"text": "let's talk about Python"},
        created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    mock_repo.search_messages.return_value = [(mock_msg, "chat-abc")]

    with (
        patch("backend.tools.chat_search.SessionLocal", return_value=mock_session),
        patch("backend.tools.chat_search.Repository", return_value=mock_repo),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        result = await tool.run({"query": "Python"}, ctx={"user_id": "u1"})

    assert result["count"] == 1
    assert "Python" in result["matches"][0]["text"]
    mock_repo.search_messages.assert_called_once_with("u1", "Python", limit=5)
