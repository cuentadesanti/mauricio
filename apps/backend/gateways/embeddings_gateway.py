import litellm

from ..core.config import settings


class EmbeddingsGateway:
    """Wrapper estable sobre el modelo de embeddings."""

    def __init__(self):
        self.model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await litellm.aembedding(model=self.model, input=texts)
        return [item["embedding"] for item in resp.data]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
