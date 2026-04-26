from unittest.mock import AsyncMock, patch

import pytest
from backend.gateways.embeddings_gateway import EmbeddingsGateway


@pytest.mark.asyncio
async def test_embed_empty_list():
    gw = EmbeddingsGateway()
    result = await gw.embed([])
    assert result == []


@pytest.mark.asyncio
async def test_embed_texts():
    mock_resp = AsyncMock()
    mock_resp.data = [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]

    with patch("litellm.aembedding", return_value=mock_resp) as mock_aembed:
        gw = EmbeddingsGateway()
        result = await gw.embed(["hello", "world"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        mock_aembed.assert_called_once()


@pytest.mark.asyncio
async def test_embed_one():
    mock_resp = AsyncMock()
    mock_resp.data = [{"embedding": [0.1, 0.2, 0.3]}]

    with patch("litellm.aembedding", return_value=mock_resp):
        gw = EmbeddingsGateway()
        result = await gw.embed_one("hello")

        assert result == [0.1, 0.2, 0.3]
