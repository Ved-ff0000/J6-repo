"""Phase 6 compatibility facade over the provider-independent embedding interface."""

from app.ai.embeddings import create_embedding_provider
from app.ai.interfaces import ProviderError
from app.core.config import Settings


class SearchFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Embedder:
    def __init__(self, settings: Settings):
        self.provider = create_embedding_provider(settings)
        self.profile = self.provider.profile
        self.collection = self.provider.collection
        self.label = self.provider.label

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self.provider.embed(texts)
        except ProviderError as exc:
            code = (
                exc.code
                if exc.code
                in {"EMBEDDING_BATCH_LIMIT", "EMBEDDING_INPUT_LIMIT", "NO_EMBEDDABLE_TEXT"}
                else "EMBEDDING_UNAVAILABLE"
            )
            raise SearchFailure(code) from exc
