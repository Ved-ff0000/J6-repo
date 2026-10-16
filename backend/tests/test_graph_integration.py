"""Real Neo4j/PostgreSQL/MinIO integration. Never substituted with a local graph double."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_auth import headers, register
from test_graph import TEXT, assertions

from app.auth.models import Organization
from app.auth.security import utcnow
from app.core.config import Settings
from app.db.session import create_engine
from app.documents.models import Document, DocumentMetadata, DocumentVersion
from app.documents.storage import S3ObjectStore
from app.graph.models import GraphSnapshot
from app.graph.worker import claim_next, owned, run_once
from app.main import create_app
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.worker import run_once as parse_once
from app.search.models import IndexJob

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires real PostgreSQL/Redis/MinIO/Neo4j; CI opts in",
)


def test_real_neo4j_projection_race_provenance_and_isolation():
    settings = Settings(
        auth_rate_limit=1000,
        processing_enabled=True,
        allow_unscanned_processing=True,
        graph_enabled=True,
    )
    slug = "graph-ci-" + uuid4().hex
    store = S3ObjectStore(settings)
    organizations = []
    with TestClient(create_app(settings)) as client:
        graph_store = client.app.state.graph_store
        try:
            pairs = [register(client, slug + suffix) for suffix in ("a", "b")]
            prefixes = []
            bodies = []
            for pair in pairs:
                organizations.append(
                    client.get("/api/v1/organizations/current", headers=headers(pair)).json()["id"]
                )
                response = client.post(
                    "/api/v1/documents",
                    headers=headers(pair),
                    files={"file": ("synthetic.txt", TEXT, "text/plain")},
                )
                assert response.status_code == 201, response.text
                prefix = f"/api/v1/documents/{response.json()['id']}/versions/1"
                prefixes.append(prefix)
                assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
                assert client.portal.call(
                    parse_once, client.app.state.session_factory, store, settings
                )
                body = assertions(
                    client.get(prefix + "/chunks", headers=headers(pair)).json()[0]["id"]
                )
                bodies.append(body)

            def create(index):
                return client.post(
                    prefixes[index] + "/graph/snapshots",
                    headers=headers(pairs[index]),
                    json={**bodies[index], "expected_revision": 0},
                )

            # Optimistic revision lock: racing writers cannot both publish revision 1.
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda _: create(0), range(2)))
            assert sorted(r.status_code for r in responses) == [202, 409]

            def claim(_):
                return client.portal.call(claim_next, client.app.state.session_factory, settings)

            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(claim, range(2)))
            assert sum(c is not None for c in claims) == 1
            first = next(c for c in claims if c is not None)

            async def recover():
                async with client.app.state.session_factory() as db, db.begin():
                    snapshot = await db.get(GraphSnapshot, first.id)
                    snapshot.lease_expires_at = utcnow() - timedelta(seconds=1)
                assert await run_once(client.app.state.session_factory, graph_store, settings)
                async with client.app.state.session_factory() as db, db.begin():
                    assert await owned(db, first) is None

            client.portal.call(recover)
            assert create(1).status_code == 202
            assert client.portal.call(
                run_once, client.app.state.session_factory, graph_store, settings
            )
            for index, pair in enumerate(pairs):
                result = client.get(prefixes[index] + "/graph", headers=headers(pair))
                assert result.status_code == 200, result.text
                assert len(result.json()["nodes"]) == 5 and len(result.json()["edges"]) == 5
                assert result.json()["assertion_status"] == "HUMAN_ASSERTED_UNVERIFIED"
                assert (
                    client.get(
                        prefixes[index] + "/graph?root_id=tenant&hops=1", headers=headers(pair)
                    ).status_code
                    == 200
                )
                assert (
                    client.get(
                        prefixes[index] + "/graph", headers=headers(pairs[1 - index])
                    ).status_code
                    == 404
                )

            async def count_and_cleanup_check():
                async with graph_store.driver.session(database=settings.neo4j_database) as session:
                    result = await session.run(
                        "MATCH (n:LeaseGuardEntity) WHERE n.organization_id IN $organizations RETURN count(n) AS count",
                        organizations=organizations,
                    )
                    record = await result.single(strict=True)
                    assert record["count"] == 10

            client.portal.call(count_and_cleanup_check)
        finally:

            async def clean_neo4j():
                async with graph_store.driver.session(database=settings.neo4j_database) as session:
                    await (
                        await session.run(
                            "MATCH (n) WHERE (n:LeaseGuardEntity OR n:LeaseGuardProjection) AND n.organization_id IN $organizations DETACH DELETE n",
                            organizations=organizations,
                        )
                    ).consume()

            try:
                client.portal.call(clean_neo4j)
            finally:

                async def cleanup():
                    engine = create_engine(settings)
                    try:
                        async with engine.begin() as connection:
                            ids = select(Organization.id).where(
                                Organization.slug.in_([slug + suffix for suffix in ("a", "b")])
                            )
                            keys = (
                                (
                                    await connection.execute(
                                        select(DocumentVersion.object_key).where(
                                            DocumentVersion.organization_id.in_(ids)
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
                                    delete(model).where(model.organization_id.in_(ids))
                                )
                            await connection.execute(
                                delete(Organization).where(Organization.id.in_(ids))
                            )
                    finally:
                        await engine.dispose()

                asyncio.run(cleanup())
