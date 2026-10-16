"""Opt-in real PostgreSQL/MinIO/Qdrant publication, claim race and tenant isolation."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from qdrant_client import models
from sqlalchemy import delete, select
from test_auth import headers, register

from app.auth.models import Organization
from app.auth.security import utcnow
from app.core.config import Settings
from app.db.session import create_engine
from app.documents.models import Document, DocumentMetadata, DocumentVersion
from app.documents.storage import S3ObjectStore
from app.graph.models import GraphSnapshot
from app.main import create_app
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.worker import run_once as parse_once
from app.search.models import IndexJob
from app.search.store import exact
from app.search.worker import claim_next, owned, run_once

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires PostgreSQL/Redis/MinIO/Qdrant; CI opts in",
)


def test_real_qdrant_publication_isolation_and_claim_race():
    settings = Settings(
        auth_rate_limit=1000,
        processing_enabled=True,
        allow_unscanned_processing=True,
        retrieval_enabled=True,
    )
    slug = "rag-ci-" + uuid4().hex
    store = S3ObjectStore(settings)
    organization_ids = []
    with TestClient(create_app(settings)) as client:
        vectors = client.app.state.vector_store
        try:
            pairs = [register(client, slug + suffix) for suffix in ("a", "b")]
            prefixes = []
            for pair in pairs:
                organization_ids.append(
                    client.get("/api/v1/organizations/current", headers=headers(pair)).json()["id"]
                )
                document = client.post(
                    "/api/v1/documents",
                    headers=headers(pair),
                    files={
                        "file": (
                            "synthetic.txt",
                            b"1. RENT\nSynthetic monthly rent is 5000.",
                            "text/plain",
                        )
                    },
                )
                assert document.status_code == 201, document.text
                prefix = f"/api/v1/documents/{document.json()['id']}/versions/1"
                prefixes.append(prefix)
                assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
                assert client.portal.call(
                    parse_once, client.app.state.session_factory, store, settings
                )
            assert client.post(prefixes[0] + "/index", headers=headers(pairs[0])).status_code == 202

            def claim_worker(_):
                return client.portal.call(claim_next, client.app.state.session_factory, settings)

            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(claim_worker, range(2)))
            assert sum(item is not None for item in claims) == 1
            first = next(item for item in claims if item is not None)

            async def recover():
                async with client.app.state.session_factory() as db, db.begin():
                    job = await db.get(IndexJob, first.id)
                    job.lease_expires_at = utcnow() - timedelta(seconds=1)
                assert await run_once(client.app.state.session_factory, vectors, settings)
                async with client.app.state.session_factory() as db, db.begin():
                    assert await owned(db, first) is None

            client.portal.call(recover)
            assert client.post(prefixes[1] + "/index", headers=headers(pairs[1])).status_code == 202
            assert client.portal.call(run_once, client.app.state.session_factory, vectors, settings)
            for index, pair in enumerate(pairs):
                result = client.post(
                    "/api/v1/search", headers=headers(pair), json={"query": "monthly rent"}
                )
                assert result.status_code == 200, result.text
                hit = result.json()["hits"][0]["citation"]
                assert f"/{hit['document_id']}/" in prefixes[index]
                assert (
                    client.get(
                        f"/api/v1/search/citations/{hit['chunk_id']}",
                        headers=headers(pairs[1 - index]),
                    ).status_code
                    == 404
                )
        finally:

            async def clean_vectors():
                if await vectors.client.collection_exists(vectors.collection):
                    for identifier in organization_ids:
                        await vectors.client.delete(
                            vectors.collection,
                            models.FilterSelector(
                                filter=models.Filter(must=[exact("organization_id", identifier)])
                            ),
                            wait=True,
                        )

            client.portal.call(clean_vectors)

            async def cleanup():
                engine = create_engine(settings)
                try:
                    async with engine.begin() as connection:
                        organizations = select(Organization.id).where(
                            Organization.slug.in_([slug + suffix for suffix in ("a", "b")])
                        )
                        keys = (
                            (
                                await connection.execute(
                                    select(DocumentVersion.object_key).where(
                                        DocumentVersion.organization_id.in_(organizations)
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        for key in keys:
                            await store.delete(key)
                        for model in (
                            GraphSnapshot,
                            IndexJob,
                            DocumentChunk,
                            DocumentPage,
                            ProcessingJob,
                            DocumentMetadata,
                            DocumentVersion,
                            Document,
                        ):
                            await connection.execute(
                                delete(model).where(model.organization_id.in_(organizations))
                            )
                        await connection.execute(
                            delete(Organization).where(Organization.id.in_(organizations))
                        )
                finally:
                    await engine.dispose()

            asyncio.run(cleanup())
