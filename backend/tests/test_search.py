"""Real Qdrant local engine + real parsers/SQLite; failure/forged-hit tests are explicit."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient, models
from test_auth import create_member, headers, login, register
from test_documents import upload
from test_processing import work

from app.auth.security import utcnow
from app.core.config import Settings
from app.processing.models import DocumentChunk
from app.search.embeddings import Embedder, SearchFailure
from app.search.models import IndexJob
from app.search.store import VectorStore, point_id
from app.search.worker import claim_next, owned, run_once

pytestmark = pytest.mark.filterwarnings("ignore:Payload indexes have no effect in the local Qdrant")


@pytest.fixture
def vectors(client, processing):
    settings = client.app.state.settings
    settings.retrieval_enabled = True
    store = VectorStore(settings, AsyncQdrantClient(":memory:"))
    client.portal.call(client.app.state.vector_store.close)
    client.app.state.vector_store = store
    return store


def index_work(client, store):
    return client.portal.call(
        run_once, client.app.state.test_factory, store, client.app.state.settings
    )


def prepare(
    client,
    processing,
    pair,
    content=b"1. RENT\nSynthetic tenant owes monthly rent 5000 dollars.",
    **labels,
):
    doc = upload(client, pair, content, **labels)
    prefix = f"/api/v1/documents/{doc['id']}/versions/1"
    assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
    assert work(client, processing)
    return doc, prefix


def query(client, pair, **filters):
    return client.post(
        "/api/v1/search", headers=headers(pair), json={"query": "monthly rent", **filters}
    )


def test_real_vectors_citations_and_tenant_filters(client, processing, vectors):
    alpha = register(client)
    beta = register(client, "beta", email="beta@example.com")
    doc, prefix = prepare(
        client, processing, alpha, document_type="LEASE", property_name="Demo property"
    )
    beta_doc, beta_prefix = prepare(
        client, processing, beta, b"Private OTHER TENANT monthly rent 8000."
    )
    first = client.post(prefix + "/index", headers=headers(alpha))
    assert first.status_code == 202 and first.json()["status"] == "QUEUED"
    assert client.post(prefix + "/index", headers=headers(alpha)).json()["id"] == first.json()["id"]
    assert query(client, alpha).json()["hits"] == []
    assert index_work(client, vectors)
    assert client.post(beta_prefix + "/index", headers=headers(beta)).status_code == 202
    assert index_work(client, vectors)
    result = query(client, alpha, document_type="LEASE", property_name="Demo property")
    assert result.status_code == 200, result.text
    citation = result.json()["hits"][0]["citation"]
    assert citation["document_id"] == doc["id"] and "OTHER TENANT" not in result.text
    assert "Lexical" in result.json()["embedding"]
    assert citation["page_number_kind"] == "LOGICAL"
    page = client.get(prefix + "/pages", headers=headers(alpha)).json()[0]
    assert page["text"][citation["start_offset"] : citation["end_offset"]] == citation["evidence"]
    assert (
        client.get(
            f"/api/v1/search/citations/{citation['chunk_id']}", headers=headers(alpha)
        ).json()
        == citation
    )
    assert (
        client.get(
            f"/api/v1/search/citations/{citation['chunk_id']}", headers=headers(beta)
        ).status_code
        == 404
    )
    assert query(client, beta, document_id=doc["id"]).status_code == 404
    assert client.post(prefix + "/index", headers=headers(beta)).status_code == 404
    assert query(client, alpha, page=2).json()["hits"] == []
    assert query(client, alpha, section="NOT A SECTION").json()["hits"] == []
    assert query(client, beta).json()["hits"][0]["citation"]["document_id"] == beta_doc["id"]
    assert result.headers["cache-control"] == "no-store"


def test_current_metadata_archive_and_history(client, processing, vectors):
    pair = register(client)
    doc, prefix = prepare(client, processing, pair, property_name="Before")
    client.post(prefix + "/index", headers=headers(pair))
    assert index_work(client, vectors)
    assert query(client, pair, property_name="Before").json()["hits"]
    assert (
        client.patch(
            f"/api/v1/documents/{doc['id']}", headers=headers(pair), json={"property_name": "After"}
        ).status_code
        == 200
    )
    assert query(client, pair, property_name="Before").json()["hits"] == []
    assert query(client, pair, property_name="After").json()["hits"]
    newer = client.post(
        f"/api/v1/documents/{doc['id']}/versions",
        headers=headers(pair),
        files={"file": ("new.txt", b"New synthetic rent 6000.", "text/plain")},
        data={"expected_version": "1"},
    )
    assert newer.status_code == 201
    assert query(client, pair).json()["hits"] == []
    assert query(client, pair, document_id=doc["id"], version_number=1).json()["hits"]
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 409
    assert (
        client.post(f"/api/v1/documents/{doc['id']}/archive", headers=headers(pair)).status_code
        == 200
    )
    assert query(client, pair, document_id=doc["id"], version_number=1).json()["hits"] == []


def test_index_requires_policy_parsing_and_writer(client, processing, vectors):
    pair = register(client)
    doc = upload(client, pair)
    prefix = f"/api/v1/documents/{doc['id']}/versions/1"
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 409
    client.post(prefix + "/process", headers=headers(pair))
    assert work(client, processing)
    create_member(client, pair, "VIEWER")
    viewer = login(client, "member@example.com")
    assert client.post(prefix + "/index", headers=headers(viewer)).status_code == 403
    settings = client.app.state.settings
    settings.allow_unscanned_processing = False
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 409
    settings.allow_unscanned_processing = True
    settings.retrieval_enabled = False
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 503
    assert query(client, pair).status_code == 503
    assert client.get("/api/v1/search/config", headers=headers(pair)).json()["enabled"] is False
    assert query(client, pair, organization_id=str(uuid4())).status_code == 422
    assert query(client, pair, version_number=1).status_code == 422
    assert query(client, pair, query=" ").status_code == 422
    assert query(client, pair, top_k=21).status_code == 422


def test_failed_partial_generation_is_invisible_and_retry_rebuild_work(client, processing, vectors):
    pair = register(client)
    _, prefix = prepare(client, processing, pair)
    client.post(prefix + "/index", headers=headers(pair))
    original = vectors.upsert

    async def fail_after_write(points):
        await original(points)
        raise SearchFailure("VECTOR_UNAVAILABLE")

    with patch.object(vectors, "upsert", side_effect=fail_after_write):
        assert index_work(client, vectors)
    assert client.get(prefix + "/index", headers=headers(pair)).json()["status"] == "FAILED"
    assert query(client, pair).json()["hits"] == []
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 202
    assert index_work(client, vectors)
    assert query(client, pair).json()["hits"]
    assert client.post(prefix + "/index", headers=headers(pair)).json()["status"] == "READY"
    assert (
        client.post(prefix + "/index?rebuild=true", headers=headers(pair)).json()["status"]
        == "QUEUED"
    )
    assert query(client, pair).json()["hits"] == []
    assert index_work(client, vectors)
    assert query(client, pair).json()["hits"]


def test_external_outage_never_becomes_empty_success(client, processing, vectors):
    pair = register(client)
    _, prefix = prepare(client, processing, pair)
    client.post(prefix + "/index", headers=headers(pair))
    assert index_work(client, vectors)
    with patch.object(
        vectors.client, "query_points", side_effect=RuntimeError("private-service-secret")
    ):
        result = query(client, pair)
    assert (
        result.status_code == 503
        and "VECTOR_UNAVAILABLE" in result.text
        and "private-service-secret" not in result.text
    )


def test_forged_hits_and_corrupted_source_fail_closed(client, processing, vectors):
    pair = register(client)
    _, prefix = prepare(client, processing, pair)
    client.post(prefix + "/index", headers=headers(pair))
    assert index_work(client, vectors)
    real = vectors.search

    async def corrupt(*args, **kwargs):
        points = await real(*args, **kwargs)
        points[0].payload["organization_id"] = str(uuid4())
        return points

    with patch.object(vectors, "search", side_effect=corrupt):
        assert query(client, pair).json()["hits"] == []
    chunk_id = query(client, pair).json()["hits"][0]["citation"]["chunk_id"]

    async def damage():
        async with client.app.state.test_factory() as db, db.begin():
            chunk = await db.get(DocumentChunk, UUID(chunk_id))
            chunk.text = "tampered synthetic text"

    client.portal.call(damage)
    assert query(client, pair).json()["hits"] == []
    assert (
        client.get(f"/api/v1/search/citations/{chunk_id}", headers=headers(pair)).status_code == 409
    )


def test_lease_reclaim_uses_new_generation_and_fences_stale_worker(client, processing, vectors):
    pair = register(client)
    _, prefix = prepare(client, processing, pair)
    client.post(prefix + "/index", headers=headers(pair))

    async def scenario():
        factory = client.app.state.test_factory
        settings = client.app.state.settings
        first = await claim_next(factory, settings)
        assert first is not None
        assert await claim_next(factory, settings) is None
        async with factory() as db, db.begin():
            job = await db.get(IndexJob, first.id)
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        second = await claim_next(factory, settings)
        assert second is not None and second.generation != first.generation
        async with factory() as db, db.begin():
            assert await owned(db, first) is None
            job = await db.get(IndexJob, first.id)
            job.attempts = settings.index_max_attempts
            job.lease_expires_at = utcnow() - timedelta(seconds=1)
        assert await claim_next(factory, settings) is None

    client.portal.call(scenario)
    assert (
        client.get(prefix + "/index", headers=headers(pair)).json()["error_code"] == "WORKER_LOST"
    )
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 409


def test_embedding_vectors_are_deterministic_and_profile_bound():
    async def scenario():
        settings = Settings()
        embedder = Embedder(settings)
        first, second = await embedder.embed(["Rent renewal", "RENT renewal"])
        assert first == second and abs(sum(v * v for v in first) - 1) < 1e-9
        assert embedder.profile != Embedder(Settings(embedding_dimensions=1024)).profile
        with pytest.raises(SearchFailure, match="NO_EMBEDDABLE_TEXT"):
            await embedder.embed(["!?!"])
        with pytest.raises(SearchFailure, match="EMBEDDING_BATCH_LIMIT"):
            await embedder.embed(["rent"] * 33)

    asyncio.run(scenario())


def test_remote_embedding_contract_and_sanitized_failure():
    async def scenario():
        embedder = Embedder(
            Settings(
                embedding_provider="openai",
                embedding_api_key=SecretStr("test-not-a-real-key"),
                embedding_dimensions=128,
            )
        )
        captured = []

        def success(request):
            captured.append(request)
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 127}]}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(success))
        with patch("httpx.AsyncClient", return_value=client):
            assert (await embedder.embed(["Synthetic rent text"]))[0][0] == 1
            assert json.loads(captured[0].content)["input"] == ["Synthetic rent text"]

        def invalid(request):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(invalid))
        with (
            patch("httpx.AsyncClient", return_value=client),
            pytest.raises(SearchFailure, match="EMBEDDING_UNAVAILABLE"),
        ):
            await embedder.embed(["text"])

    asyncio.run(scenario())


def test_vector_schema_mismatch_is_not_recreated(client, processing, vectors):
    async def check():
        await vectors.client.create_collection(
            vectors.collection,
            vectors_config=models.VectorParams(size=128, distance=models.Distance.COSINE),
        )
        with pytest.raises(SearchFailure, match="VECTOR_SCHEMA_MISMATCH"):
            await vectors.provision()

    client.portal.call(check)


def test_spoofed_vector_hit_cannot_expose_other_tenant_source(client, processing, vectors):
    alpha = register(client)
    beta = register(client, "beta")
    _, first = prepare(client, processing, alpha)
    _, second = prepare(client, processing, beta, b"Private tenant beta monthly rent.")
    for pair, prefix in ((alpha, first), (beta, second)):
        client.post(prefix + "/index", headers=headers(pair))
        assert index_work(client, vectors)
    private_chunk = query(client, beta).json()["hits"][0]["citation"]["chunk_id"]
    real = vectors.search

    async def spoof(*args, **kwargs):
        points = await real(*args, **kwargs)
        points[0].payload["chunk_id"] = private_chunk
        points[0].id = point_id(UUID(points[0].payload["generation"]), UUID(private_chunk))
        return points

    with patch.object(vectors, "search", side_effect=spoof):
        result = query(client, alpha)
    assert (
        result.status_code == 200
        and result.json()["hits"] == []
        and "Private tenant beta" not in result.text
    )


def test_archive_during_vector_lookup_revalidates_database(client, processing, vectors):
    pair = register(client)
    doc, prefix = prepare(client, processing, pair)
    client.post(prefix + "/index", headers=headers(pair))
    assert index_work(client, vectors)
    real = vectors.search

    async def archive(*args, **kwargs):
        from app.documents.models import Document, DocumentStatus

        result = await real(*args, **kwargs)
        async with client.app.state.test_factory() as db, db.begin():
            document = await db.get(Document, UUID(doc["id"]))
            document.status = DocumentStatus.ARCHIVED
        return result

    with patch.object(vectors, "search", side_effect=archive):
        assert query(client, pair).json()["hits"] == []
