"""Real PostgreSQL + private S3/MinIO publication, isolation and concurrency tests."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth.models import Organization
from app.auth.security import utcnow
from app.core.config import Settings
from app.db.session import create_engine
from app.documents.models import Document, DocumentMetadata, DocumentVersion
from app.documents.storage import S3ObjectStore
from app.graph.models import GraphSnapshot
from app.main import create_app
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.worker import claim_next, owned, run_once
from app.search.models import IndexJob

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires PostgreSQL, Redis and initialized S3 bucket; CI opts in",
)


def test_s3_document_publication_and_concurrent_versions() -> None:
    slug = f"doc-ci-{uuid4().hex}"
    settings = Settings(
        auth_rate_limit=1000, processing_enabled=True, allow_unscanned_processing=True
    )
    store = S3ObjectStore(settings)
    try:
        with TestClient(create_app(settings)) as client:
            pairs = []
            for suffix in ("alpha", "beta"):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{suffix}@{slug}.example.com",
                        "password": "Correct-horse-lease-42",
                        "first_name": "CI",
                        "last_name": "Admin",
                        "organization_name": f"{slug}-{suffix}",
                        "organization_slug": f"{slug}-{suffix}",
                    },
                )
                assert response.status_code == 201, response.text
                pairs.append(response.json())
            headers = [{"Authorization": f"Bearer {pair['access_token']}"} for pair in pairs]
            response = client.post(
                "/api/v1/documents",
                headers=headers[0],
                files={"file": ("lease.txt", b"Synthetic original lease", "text/plain")},
            )
            assert response.status_code == 201, response.text
            document = response.json()
            prefix = f"/api/v1/documents/{document['id']}"
            assert (
                client.get(prefix + "/versions/1/download", headers=headers[0]).content
                == b"Synthetic original lease"
            )
            assert (
                client.get(prefix + "/versions/1/download", headers=headers[1]).status_code == 404
            )
            assert client.get("/api/v1/documents", headers=headers[1]).json()["total"] == 0
            scheduled = client.post(prefix + "/versions/1/process", headers=headers[0])
            assert scheduled.status_code == 202, scheduled.text
            # Run the worker on the app's loop to avoid sharing asyncpg pools across loops.
            assert client.portal.call(run_once, client.app.state.session_factory, store, settings)
            assert (
                client.get(prefix + "/versions/1/job", headers=headers[0]).json()["status"]
                == "SUCCEEDED"
            )
            assert (
                client.get(prefix + "/versions/1/chunks", headers=headers[0]).json()[0]["text"]
                == "Synthetic original lease"
            )
            assert client.get(prefix + "/versions/1/chunks", headers=headers[1]).status_code == 404

            def version(number: int):
                return client.post(
                    prefix + "/versions",
                    headers=headers[0],
                    files={
                        "file": (
                            "amendment.txt",
                            f"Synthetic amendment {number}".encode(),
                            "text/plain",
                        )
                    },
                    data={"expected_version": "1"},
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(version, range(2)))
            assert sorted(response.status_code for response in responses) == [201, 409]
            assert client.get(prefix, headers=headers[0]).json()["current_version"] == 2
            assert len(client.get(prefix + "/versions", headers=headers[0]).json()) == 2
            assert (
                client.post(prefix + "/versions/2/process", headers=headers[0]).status_code == 202
            )

            def claim_worker(_: int):
                return client.portal.call(claim_next, client.app.state.session_factory, settings)

            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(claim_worker, range(2)))
            assert sum(claim is not None for claim in claims) == 1
            claimed = next(claim for claim in claims if claim is not None)

            async def expire_and_fence():
                async with client.app.state.session_factory() as db, db.begin():
                    job = await db.get(ProcessingJob, claimed.id)
                    job.lease_expires_at = utcnow() - timedelta(seconds=1)
                assert await run_once(client.app.state.session_factory, store, settings)
                async with client.app.state.session_factory() as db, db.begin():
                    assert await owned(db, claimed) is None

            client.portal.call(expire_and_fence)
            assert (
                client.get(prefix + "/versions/2/job", headers=headers[0]).json()["status"]
                == "SUCCEEDED"
            )
            assert (
                client.get(prefix + "/versions/1/download", headers=headers[0]).content
                == b"Synthetic original lease"
            )
    finally:

        async def cleanup() -> None:
            engine = create_engine(settings)
            try:
                async with engine.begin() as connection:
                    organization_ids = select(Organization.id).where(
                        Organization.slug.like(f"{slug}-%")
                    )
                    rows = (
                        (
                            await connection.execute(
                                select(DocumentVersion.object_key).where(
                                    DocumentVersion.organization_id.in_(organization_ids)
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for key in rows:
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
                            delete(model).where(model.organization_id.in_(organization_ids))
                        )
                    await connection.execute(
                        delete(Organization).where(Organization.slug.like(f"{slug}-%"))
                    )
            finally:
                await engine.dispose()

        asyncio.run(cleanup())
