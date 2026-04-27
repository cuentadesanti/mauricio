"""Integration test for the /v1/voice/turn endpoint.

Uses the real FastAPI app with mocked LLM + embedding gateways.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_voice_turn_endpoint_requires_auth():
    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/voice/turn", json={"satellite_id": "x", "transcript": "hi"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_satellite_state_endpoint():
    from backend.main import app

    transport = ASGITransport(app=app)

    # Mock DB session to avoid real Postgres
    mock_session = AsyncMock()
    mock_repo = AsyncMock()

    from unittest.mock import MagicMock

    mock_sat = MagicMock(mode="home_assistant", active_chat_id=None)
    mock_repo.get_or_create_user.return_value = MagicMock(id="u1")
    mock_repo.get_or_create_satellite.return_value = mock_sat

    with (
        patch("backend.api.voice.get_session", return_value=mock_session),
        patch("backend.api.voice.Repository", return_value=mock_repo),
    ):
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Make get_session a proper async generator
        async def fake_get_session():
            yield mock_session

        with patch("backend.api.voice.get_session", fake_get_session):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.get(
                    "/v1/voice/satellite/test-sat/state",
                    headers={"Authorization": "Bearer test_key_v0"},
                )

    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "home_assistant"
    assert data["active_chat_id"] is None
