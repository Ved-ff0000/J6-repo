"""Qdrant stores vectors and source identifiers only. Source text lives in PostgreSQL."""

from typing import Any
from uuid import UUID, uuid5

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.search.embeddings import Embedder, SearchFailure


def point_id(generation: UUID, chunk_id: UUID) -> str:
    return str(uuid5(generation, str(chunk_id)))


def exact(key: str, value: str | int) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


class VectorStore:
    def __init__(self, settings: Settings, client: AsyncQdrantClient | None = None):
        self.collection = Embedder(settings).collection
        self.dimensions = settings.embedding_dimensions
        self.client = client or AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
            timeout=15,
            check_compatibility=False,
            trust_env=False,
        )

    async def close(self) -> None:
        await self.client.close()

    async def healthy(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:  # noqa: BLE001 - fail closed; never expose service exceptions
            return False

    async def provision(self) -> None:
        try:
            if not await self.client.collection_exists(self.collection):
                try:
                    await self.client.create_collection(
                        self.collection,
                        vectors_config=models.VectorParams(
                            size=self.dimensions, distance=models.Distance.COSINE
                        ),
                    )
                except Exception:
                    if not await self.client.collection_exists(self.collection):
                        raise
            info = await self.client.get_collection(self.collection)
            config = info.config.params.vectors
            if (
                not isinstance(config, models.VectorParams)
                or config.size != self.dimensions
                or config.distance != models.Distance.COSINE
            ):
                raise SearchFailure("VECTOR_SCHEMA_MISMATCH")
            # Server-side indexes are required for bounded tenant/generation filters.
            for key in ("organization_id", "generation", "version_id"):
                await self.client.create_payload_index(
                    self.collection, key, models.PayloadSchemaType.KEYWORD, wait=True
                )
        except SearchFailure:
            raise
        except Exception as exc:
            raise SearchFailure("VECTOR_UNAVAILABLE") from exc

    async def upsert(self, points: list[models.PointStruct]) -> None:
        try:
            result = await self.client.upsert(self.collection, points=points, wait=True)
            if result.status != models.UpdateStatus.COMPLETED:
                raise SearchFailure("VECTOR_UNAVAILABLE")
        except Exception as exc:
            raise SearchFailure("VECTOR_UNAVAILABLE") from exc

    async def search(
        self,
        vector: list[float],
        organization_id: UUID,
        generations: list[str],
        *,
        limit: int,
        page: int | None = None,
        section: str | None = None,
    ) -> list[models.ScoredPoint]:
        if not generations:
            return []
        conditions: list[Any] = [
            exact("organization_id", str(organization_id)),
            models.FieldCondition(key="generation", match=models.MatchAny(any=generations)),
        ]
        if page is not None:
            conditions.append(exact("page", page))
        if section is not None:
            conditions.append(exact("section", section))
        try:
            result = await self.client.query_points(
                self.collection,
                query=vector,
                query_filter=models.Filter(must=conditions),
                limit=limit,
                with_payload=True,
                with_vectors=False,
                score_threshold=0.000001,
            )
            return result.points
        except Exception as exc:
            raise SearchFailure("VECTOR_UNAVAILABLE") from exc
