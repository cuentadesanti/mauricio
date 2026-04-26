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
