"""SQL/source lifecycle tests; graph doubles below are ONLY test contracts, not a Neo4j engine."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError
from test_auth import create_member, headers, login, register
from test_documents import upload
from test_processing import work

from app.auth.security import utcnow
from app.core.config import Settings
from app.graph.models import GraphSnapshot
from app.graph.schemas import Assertions
from app.graph.service import GraphFailure
from app.graph.store import GraphStore, Projection, Scope
from app.graph.worker import claim_next, owned, run_once
from app.processing.models import DocumentChunk

TEXT = b"Lease Alpha\nTenant Beta\nLandlord Gamma\nProperty Delta\nClause Rent\n"


def assertions(chunk):
    nodes = [
        {"id": key, "kind": kind, "name": name, "evidence_chunk_ids": [chunk]}
        for key, kind, name in (
            ("lease", "Lease", "Lease Alpha"),
            ("tenant", "Tenant", "Tenant Beta"),
            ("landlord", "Landlord", "Landlord Gamma"),
            ("property", "Property", "Property Delta"),
            ("clause", "Clause", "Clause Rent"),
        )
    ]
    edges = [
        {"id": key, "kind": kind, "source": source, "target": target, "evidence_chunk_ids": [chunk]}
        for key, kind, source, target in (
            ("lease_tenant", "HAS_LEASE", "tenant", "lease"),
            ("location", "LOCATED_AT", "lease", "property"),
            ("ownership", "OWNS", "landlord", "property"),
            ("landlord_lease", "LANDLORD_OF", "landlord", "lease"),
            ("rent", "HAS_CLAUSE", "lease", "clause"),
        )
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "rationale": "User-proposed relationships for a synthetic source; not legally verified.",
    }


class ContractGraph:
    """Test-only projection double. No claim that these tests execute Neo4j."""

    def __init__(self):
        self.generations = {}

    async def close(self):
        pass

    async def provision(self):
        pass

    async def healthy(self):
        return True

    async def project(self, scope, data):
        self.generations[scope] = Projection(
            content_hash=data.digest(), nodes=data.nodes, edges=data.edges
        )

    async def read(self, scope):
        if scope not in self.generations:
            raise GraphFailure("GRAPH_NOT_FOUND")
        return self.generations[scope].model_copy(deep=True)


@pytest.fixture
def graph_store(client, processing):
    client.app.state.settings.graph_enabled = True
    client.app.state.settings.neo4j_password = SecretStr("test-only-password")
    client.app.state.graph_store = ContractGraph()
    return client.app.state.graph_store


def prepare(client, processing, pair, **kwargs):
    doc = upload(client, pair, TEXT, **kwargs)
    prefix = f"/api/v1/documents/{doc['id']}/versions/1"
    assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
    assert work(client, processing)
    chunk = client.get(prefix + "/chunks", headers=headers(pair)).json()[0]["id"]
    return doc, prefix, assertions(chunk)


def propose(client, pair, prefix, data, revision=0):
    return client.post(
        prefix + "/graph/snapshots",
        headers=headers(pair),
        json={**data, "expected_revision": revision},
    )


def graph_work(client, store):
    return client.portal.call(
        run_once, client.app.state.test_factory, store, client.app.state.settings
    )


def test_proposal_revision_and_projection_lifecycle(client, processing, graph_store):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    assert client.get(prefix + "/graph/snapshot", headers=headers(pair)).json() is None
    created = propose(client, pair, prefix, data)
    assert created.status_code == 202, created.text
    assert (
        created.json()["status"] == "QUEUED"
        and created.json()["assertion_status"] == "HUMAN_ASSERTED_UNVERIFIED"
    )
    assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409
    assert propose(client, pair, prefix, data).status_code == 409
    assert graph_work(client, graph_store)
    graph = client.get(prefix + "/graph", headers=headers(pair))
    assert graph.status_code == 200, graph.text
    assert len(graph.json()["nodes"]) == 5 and len(graph.json()["edges"]) == 5
    assert graph.headers["cache-control"] == "no-store"
    assert client.get(prefix + "/graph?root_id=tenant&hops=1", headers=headers(pair)).json()[
        "nodes"
    ] == [data["nodes"][0], data["nodes"][1]]
    assert (
        len(
            client.get(prefix + "/graph?root_id=tenant&hops=2", headers=headers(pair)).json()[
                "nodes"
            ]
        )
        == 5
    )
    assert client.get(prefix + "/graph?root_id=missing", headers=headers(pair)).status_code == 404
    assert (
        client.post(prefix + "/graph/snapshots/1/project", headers=headers(pair)).json()["status"]
        == "READY"
    )
    newer = propose(client, pair, prefix, data, 1)
    assert newer.status_code == 202
    assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409
    assert client.get(prefix + "/graph?revision=1", headers=headers(pair)).status_code == 200
    assert len(client.get(prefix + "/graph/snapshots", headers=headers(pair)).json()) == 2


def test_wrong_tenant_evidence_and_projection_never_escape(client, processing, graph_store):
    alpha = register(client)
    beta = register(client, "beta")
    _, first, data = prepare(client, processing, alpha)
    _, second, private = prepare(client, processing, beta)
    assert propose(client, beta, first, data).status_code == 404
    assert propose(client, alpha, first, private).status_code == 404
    assert propose(client, alpha, first, data).status_code == 202
    assert graph_work(client, graph_store)
    assert client.get(first + "/graph", headers=headers(beta)).status_code == 404
    assert client.get(first + "/graph/snapshot", headers=headers(beta)).status_code == 404
    assert propose(client, beta, second, private).status_code == 202
    assert graph_work(client, graph_store)
    scopes = list(graph_store.generations)
    with patch.object(
        graph_store,
        "read",
        return_value=graph_store.generations[scopes[-1]].model_copy(
            update={"content_hash": "forged"}
        ),
    ):
        result = client.get(first + "/graph", headers=headers(alpha))
    assert result.status_code == 409 and "Tenant Beta" not in result.text


def test_graph_validation_rejects_unsupported_facts_and_injection_kinds(
    client, processing, graph_store
):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    for mutate in (
        lambda d: d["nodes"][0].update(kind="Lease) DETACH DELETE n"),
        lambda d: d["edges"][0].update(source="lease"),
        lambda d: d["nodes"][0].update(verified=True),
        lambda d: d.update(organization_id=str(uuid4())),
        lambda d: d["nodes"][0].update(evidence_chunk_ids=[]),
    ):
        invalid = deepcopy(data)
        mutate(invalid)
        assert propose(client, pair, prefix, invalid).status_code == 422
    invalid = deepcopy(data)
    invalid["nodes"][0]["name"] = "Not present in source"
    assert propose(client, pair, prefix, invalid).status_code == 422
    invalid = deepcopy(data)
    invalid["nodes"][0]["evidence_chunk_ids"] = [str(uuid4())]
    assert propose(client, pair, prefix, invalid).status_code == 404


def test_rbac_disabled_unscanned_and_unparsed_policies(client, processing, graph_store):
    pair = register(client)
    doc, prefix, data = prepare(client, processing, pair)
    create_member(client, pair, "VIEWER")
    viewer = login(client, "member@example.com")
    assert propose(client, viewer, prefix, data).status_code == 403
    client.app.state.settings.allow_unscanned_processing = False
    assert propose(client, pair, prefix, data).status_code == 409
    client.app.state.settings.allow_unscanned_processing = True
    client.app.state.settings.graph_enabled = False
    assert propose(client, pair, prefix, data).status_code == 503
    client.app.state.settings.graph_enabled = True
    response = client.post(
        f"/api/v1/documents/{doc['id']}/versions",
        headers=headers(pair),
        files={"file": ("new.txt", TEXT + b"new", "text/plain")},
        data={"expected_version": "1"},
    )
    assert response.status_code == 201
    assert propose(client, pair, prefix, data).status_code == 409
    assert (
        propose(client, pair, prefix.replace("versions/1", "versions/2"), data).status_code == 409
    )


def test_failure_retry_and_rebuild_do_not_publish_partial_generations(
    client, processing, graph_store
):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    propose(client, pair, prefix, data)
    original = graph_store.project

    async def fail(scope, body):
        await original(scope, body)
        raise GraphFailure("GRAPH_UNAVAILABLE")

    with patch.object(graph_store, "project", side_effect=fail):
        assert graph_work(client, graph_store)
    assert (
        client.get(prefix + "/graph/snapshot", headers=headers(pair)).json()["error_code"]
        == "GRAPH_UNAVAILABLE"
    )
    assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409
    assert (
        client.post(prefix + "/graph/snapshots/1/project", headers=headers(pair)).status_code == 202
    )
    assert graph_work(client, graph_store)
    assert len(graph_store.generations) == 2
    with patch.object(graph_store, "read", side_effect=GraphFailure("GRAPH_UNAVAILABLE")):
        assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 503
    assert (
        client.post(
            prefix + "/graph/snapshots/1/project?rebuild=true", headers=headers(pair)
        ).json()["status"]
        == "QUEUED"
    )
    assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409
    assert graph_work(client, graph_store)


def test_superseded_revision_stale_claim_and_retry_cap(client, processing, graph_store):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    propose(client, pair, prefix, data)

    async def scenario():
        factory = client.app.state.test_factory
        settings = client.app.state.settings
        first = await claim_next(factory, settings)
        assert first is not None
        assert await claim_next(factory, settings) is None
        async with factory() as db, db.begin():
            row = await db.get(GraphSnapshot, first.id)
            row.lease_expires_at = utcnow() - timedelta(seconds=1)
        second = await claim_next(factory, settings)
        assert second is not None and first.generation != second.generation
        async with factory() as db, db.begin():
            assert await owned(db, first) is None
            row = await db.get(GraphSnapshot, first.id)
            row.attempts = settings.graph_max_attempts
            row.lease_expires_at = utcnow() - timedelta(seconds=1)
        assert await claim_next(factory, settings) is None

    client.portal.call(scenario)
    assert (
        client.get(prefix + "/graph/snapshot", headers=headers(pair)).json()["error_code"]
        == "WORKER_LOST"
    )
    assert (
        client.post(prefix + "/graph/snapshots/1/project", headers=headers(pair)).status_code == 409
    )
    assert propose(client, pair, prefix, data, 1).status_code == 202
    assert propose(client, pair, prefix, data, 2).status_code == 202
    assert graph_work(client, graph_store)
    # A subsequent poll also drains superseded candidates if timestamp ties let
    # the latest revision be claimed first; SQLite timestamps have second precision.
    assert graph_work(client, graph_store) is False
    history = client.get(prefix + "/graph/snapshots", headers=headers(pair)).json()
    assert history[1]["error_code"] == "SUPERSEDED" and history[0]["status"] == "READY"


def test_source_corruption_prevents_graph_publication(client, processing, graph_store):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    propose(client, pair, prefix, data)

    async def corrupt():
        async with client.app.state.test_factory() as db, db.begin():
            chunk = await db.get(DocumentChunk, UUID(data["nodes"][0]["evidence_chunk_ids"][0]))
            chunk.text = "Tampered source"

    client.portal.call(corrupt)
    assert graph_work(client, graph_store)
    assert graph_store.generations == {}
    assert (
        client.get(prefix + "/graph/snapshot", headers=headers(pair)).json()["error_code"]
        == "SOURCE_MISMATCH"
    )


def test_archive_during_neo4j_read_and_projection_fails_closed(client, processing, graph_store):
    from app.documents.models import Document, DocumentStatus

    pair = register(client)
    doc, prefix, data = prepare(client, processing, pair)
    propose(client, pair, prefix, data)
    assert graph_work(client, graph_store)
    real = graph_store.read

    async def archive(scope):
        result = await real(scope)
        async with client.app.state.test_factory() as db, db.begin():
            document = await db.get(Document, UUID(doc["id"]))
            document.status = DocumentStatus.ARCHIVED
        return result

    with patch.object(graph_store, "read", side_effect=archive):
        assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409


def test_real_driver_contract_uses_parameters_not_names_in_cypher():
    data = assertions(str(uuid4()))
    data["nodes"][0]["name"] = "Lease 'quoted' { literal }"
    body = Assertions.model_validate(data)
    tx = MagicMock()
    tx.__aenter__.return_value = tx
    tx.commit = AsyncMock()
    calls = []

    async def execute(query, **params):
        calls.append((query, params))
        result = MagicMock()
        result.consume = AsyncMock()
        result.single = AsyncMock(return_value={"count": len(params.get("rows", []))})
        return result

    tx.run = AsyncMock(side_effect=execute)
    session = MagicMock()
    session.__aenter__.return_value = session
    session.begin_transaction = AsyncMock(return_value=tx)
    driver = MagicMock()
    driver.session.return_value = session
    store = GraphStore(Settings(), driver)
    scope = Scope(*[uuid4() for _ in range(5)])
    asyncio.run(store.project(scope, body))
    assert tx.commit.await_count == 1
    assert all("Lease 'quoted'" not in query for query, _ in calls)
    assert all(params.get("scope") == scope.params() for _, params in calls)
    assert any(
        params.get("rows", [{}])[0].get("name") == "Lease 'quoted' { literal }"
        for _, params in calls
    )


def test_graph_configuration_is_fail_closed():
    for settings in (
        {"graph_enabled": True},
        {"neo4j_uri": "bolt://user:password@example.com"},
        {"neo4j_uri": "bolt://example.com?tenant=other"},
        {"graph_timeout_seconds": 100, "graph_lease_seconds": 120},
    ):
        with pytest.raises(ValidationError):
            Settings(**settings)


def test_revoked_proposer_cannot_project_pending_assertions(client, processing, graph_store):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    member = create_member(client, pair, "ANALYST")
    analyst = login(client, "member@example.com")
    assert propose(client, analyst, prefix, data).status_code == 202
    assert (
        client.patch(
            f"/api/v1/users/{member['id']}", headers=headers(pair), json={"status": "DISABLED"}
        ).status_code
        == 200
    )
    assert graph_work(client, graph_store) is False
    assert (
        client.get(prefix + "/graph/snapshot", headers=headers(pair)).json()["error_code"]
        == "AUTHORIZATION_REVOKED"
    )
    assert graph_store.generations == {}


def test_projection_timeout_is_failure_not_ready(client, processing, graph_store):
    pair = register(client)
    _, prefix, data = prepare(client, processing, pair)
    propose(client, pair, prefix, data)
    client.app.state.settings.graph_timeout_seconds = 0.01

    async def slow(*args):
        await asyncio.sleep(1)

    with patch.object(graph_store, "project", side_effect=slow):
        assert graph_work(client, graph_store)
    assert (
        client.get(prefix + "/graph/snapshot", headers=headers(pair)).json()["error_code"]
        == "GRAPH_TIMEOUT"
    )
    assert client.get(prefix + "/graph", headers=headers(pair)).status_code == 409


def test_real_driver_read_contract_checks_scope_and_rejects_unknown_relationships():
    from neo4j import Record

    body = Assertions.model_validate(assertions(str(uuid4())))
    scope = Scope(*[uuid4() for _ in range(5)])

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        async def single(self, **kwargs):
            return Record(self.rows[0]) if self.rows else None

        def __aiter__(self):
            async def items():
                for row in self.rows:
                    yield Record(row)

            return items()

    tx = MagicMock()
    tx.__aenter__.return_value = tx
    tx.run = AsyncMock(
        side_effect=[
            Rows([{"hash": body.digest()}]),
            Rows([n.model_dump(mode="json") for n in body.nodes]),
            Rows([e.model_dump(mode="json") for e in body.edges]),
        ]
    )
    session = MagicMock()
    session.__aenter__.return_value = session
    session.begin_transaction = AsyncMock(return_value=tx)
    driver = MagicMock()
    driver.session.return_value = session
    store = GraphStore(Settings(), driver)
    projected = asyncio.run(store.read(scope))
    assert projected.nodes == body.nodes and projected.edges == body.edges
    for call in tx.run.call_args_list:
        assert call.kwargs["parameters"] == scope.params()
        assert "$organization_id" in call.args[0] and "$scope_key" in call.args[0]
    bad = [e.model_dump(mode="json") for e in body.edges]
    bad[0]["kind"] = "UNRECOGNIZED_RELATIONSHIP"
    tx.run = AsyncMock(
        side_effect=[
            Rows([{"hash": body.digest()}]),
            Rows([n.model_dump(mode="json") for n in body.nodes]),
            Rows(bad),
        ]
    )
    with pytest.raises(GraphFailure, match="GRAPH_INTEGRITY_ERROR"):
        asyncio.run(store.read(scope))
