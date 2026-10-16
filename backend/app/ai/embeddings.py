"""Profile-compatible real lexical vectors and OpenAI-compatible semantic embeddings."""

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter

from app.ai.interfaces import EmbeddingProvider, ProviderError
from app.ai.transport import post_json
from app.core.config import Settings


class BaseEmbedding:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dimensions = settings.embedding_dimensions
        # Preserve Phase 6 identifiers exactly; a provider-interface refactor must not reindex.
        specification: dict[str, object] = {
            "revision": "embedding-v1",
            "provider": settings.embedding_provider,
            "dimensions": self.dimensions,
        }
        if settings.embedding_provider == "openai":
            specification.update(
                model=settings.embedding_model, endpoint=settings.embedding_url.rstrip("/")
            )
        self.profile = hashlib.sha256(
            json.dumps(specification, sort_keys=True).encode()
        ).hexdigest()
        self.collection = "leaseguard_" + self.profile[:24]
        self.label = (
            "Lexical hashing v1 (not neural semantic search)"
            if settings.embedding_provider == "hashing"
            else "OpenAI-compatible: " + settings.embedding_model
        )

    def check(self, texts: list[str]) -> None:
        if not texts or len(texts) > 32:
            raise ProviderError("EMBEDDING_BATCH_LIMIT")
        if any(not isinstance(text, str) or not text or len(text) > 8000 for text in texts):
            raise ProviderError("EMBEDDING_INPUT_LIMIT")


class HashingEmbeddingProvider(BaseEmbedding):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.check(texts)
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            tokens = Counter(re.findall(r"\w+", unicodedata.normalize("NFC", text).casefold()))
            for token, count in tokens.items():
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:8], "big") % len(vector)] += (
                    1 if digest[8] & 1 else -1
                ) * (1 + math.log(count))
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0:
                raise ProviderError("NO_EMBEDDABLE_TEXT")
            vectors.append([value / norm for value in vector])
        return vectors


class OpenAIEmbeddingProvider(BaseEmbedding):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.check(texts)
        key = self.settings.embedding_api_key
        if key is None:
            raise ProviderError("PROVIDER_DISABLED")
        payload = await post_json(
            self.settings.embedding_url.rstrip("/") + "/embeddings",
            key,
            {
                "model": self.settings.embedding_model,
                "dimensions": self.dimensions,
                "input": texts,
                "encoding_format": "float",
            },
            timeout=30,
            max_bytes=8 * 1024 * 1024,
        )
        try:
            data = payload["data"]
            if (
                not isinstance(data, list)
                or len(data) != len(texts)
                or any(type(item["index"]) is not int for item in data)
                or sorted(item["index"] for item in data) != list(range(len(texts)))
            ):
                raise ValueError("Invalid batch")
            vectors = [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]
            for vector in vectors:
                if (
                    not isinstance(vector, list)
                    or len(vector) != self.dimensions
                    or not all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                    or not any(vector)
                    or not math.isfinite(math.sqrt(sum(float(v) * float(v) for v in vector)))
                ):
                    raise ValueError("Invalid vector")
            return vectors
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise ProviderError("PROVIDER_INVALID_RESPONSE") from exc


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return (
        HashingEmbeddingProvider(settings)
        if settings.embedding_provider == "hashing"
        else OpenAIEmbeddingProvider(settings)
    )
