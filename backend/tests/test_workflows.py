"""Real LangGraph, parsers, SQLite and Qdrant local; explicitly mocked model HTTP only."""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from test_ai import TestLimiter, reply
from test_auth import create_member, headers, login, register
from test_search import index_work, prepare

from app.auth.models import User
from app.core.config import Settings
from app.documents.models import Document, DocumentStatus
from app.search.store import VectorStore
from app.workflows.agent import STAGES, AgentState
from app.workflows.models import WorkflowRun

pytestmark = pytest.mark.filterwarnings("ignore:Payload indexes have no effect in the local Qdrant")
TEXT = b"COMMERCIAL LEASE\n1. RENT\nSynthetic monthly rent 5000 dollars."


@pytest.fixture
def workflow(client, processing):
    settings = client.app.state.settings
    settings.retrieval_enabled = True
    settings.llm_enabled = True
    settings.llm_api_key = SecretStr("synthetic-key-not-real")
    settings.workflow_enabled = True
    settings.llm_probe_limit = 20
    client.app.state.session_factory = client.app.state.test_factory
    client.app.state.redis = TestLimiter()
    store = VectorStore(settings, AsyncQdrantClient(":memory:"))
    client.portal.call(client.app.state.vector_store.close)
    client.app.state.vector_store = store
    pair = register(client)
    doc, prefix = prepare(client, processing, pair, TEXT)
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 202
    assert index_work(client, store)
    return client, pair, doc, prefix


@contextmanager
def model_http(kind="LEASE", alter=None):
    requests = []
    original = httpx.AsyncClient

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        sources = json.loads(body["messages"][-1]["content"].split("SOURCE_JSON:\n", 1)[1])
        is_classification = "kind" in body["response_format"]["json_schema"]["schema"]["properties"]
        data = (
            {"kind": kind}
            if is_classification
            else {
                "candidates": [
                    {
                        "kind": "document_title",
                        "value": "COMMERCIAL LEASE",
                        "chunk_id": sources[0]["chunk_id"],
                        "quote": "COMMERCIAL LEASE",
                    }
                ]
            }
        )
        if alter:
            data = await alter(data, is_classification)
        return httpx.Response(200, json=reply(json.dumps(data)))

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    ):
        yield requests


def start(workflow, key=None, **extra):
    client, pair, _doc, prefix = workflow
    return client.post(
        prefix + "/workflow-runs",
        headers=headers(pair),
        json={"request_key": str(key or uuid4()), "acknowledge_external_request": True, **extra},
    )


def test_real_langgraph_pipeline_with_exact_evidence_and_recorded_usage(workflow):
    client, pair, _doc, prefix = workflow
    with model_http() as requests:
        response = start(workflow)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "REVIEW_REQUIRED", response.text
    assert [entry["name"] for entry in run["trace"] if entry["status"] == "COMPLETED"] == list(
        STAGES
    )
    assert len(requests) == 2 and len(run["calls"]) == 2
    assert sum(item["usage"]["total_tokens"] for item in run["calls"]) == 120
    assert all(item["prompt_sha256"] and item["model_version"] is None for item in run["calls"])
    result = run["result"]
    assert result["decision"] == "REVIEW_REQUIRED" and result["review_queue_created"] is False
    assert (
        result["confidence"]["semantic_score"] is None
        and result["confidence"]["evidence_coverage"] == 1
    )
    candidate = result["candidates"][0]
    assert candidate["assertion"] == "MODEL_PROPOSED_UNVERIFIED"
    page = client.get(prefix + "/pages", headers=headers(pair)).json()[0]
    assert page["text"][candidate["quote_start"] : candidate["quote_end"]] == "COMMERCIAL LEASE"
    assert client.get(prefix + "/workflow-runs", headers=headers(pair)).json()[0]["id"] == run["id"]
    assert (
        client.get("/api/v1/workflow-runs/" + run["id"], headers=headers(pair)).json()["id"]
        == run["id"]
    )
    assert (
        "synthetic-key-not-real" not in response.text
        and response.headers["cache-control"] == "no-store"
    )


@pytest.mark.parametrize("kind", ["OTHER", "UNKNOWN"])
def test_unknown_classification_skips_retrieval_and_extraction_truthfully(workflow, kind):
    with model_http(kind) as calls:
        response = start(workflow)
    run = response.json()
    assert run["status"] == "REVIEW_REQUIRED", response.text
    assert len(calls) == 1 and len(run["calls"]) == 1 and run["result"]["candidates"] == []
    assert [entry["name"] for entry in run["trace"] if entry["status"] == "COMPLETED"] == [
        "load_document",
        "classify_document",
        "calculate_confidence",
        "decision",
    ]
    assert run["result"]["confidence"]["evidence_coverage"] is None


@pytest.mark.parametrize(
    "attack", ["foreign_chunk", "invented_quote", "value_not_in_quote", "duplicate", "auto_approve"]
)
def test_untrusted_candidate_outputs_cannot_publish_invalid_or_approved_results(workflow, attack):
    async def alter(data, is_classification):
        if not is_classification:
            if attack == "foreign_chunk":
                data["candidates"][0]["chunk_id"] = str(uuid4())
            if attack == "invented_quote":
                data["candidates"][0]["quote"] = "COMMERCIAL LEASE invented absent text"
            if attack == "value_not_in_quote":
                data["candidates"][0]["value"] = "Invented title"
            if attack == "duplicate":
                data["candidates"].append(data["candidates"][0])
            if attack == "auto_approve":
                data["decision"] = "AUTO_VALIDATE"
        return data

    with model_http(alter=alter) as calls:
        response = start(workflow)
    run = response.json()
    assert run["status"] == "FAILED" and run["result"] is None, response.text
    assert len(calls) == 2 and run["error_code"] in {
        "WORKFLOW_EVIDENCE_INVALID",
        "WORKFLOW_DUPLICATE_CANDIDATE",
        "PROVIDER_SCHEMA_INVALID",
    }


def test_replay_success_and_unknown_never_repeat_paid_calls(workflow):
    client, _pair, _doc, _prefix = workflow
    key = uuid4()
    with model_http() as calls:
        first = start(workflow, key)
    assert len(calls) == 2
    with patch("httpx.AsyncClient") as network:
        replay = start(workflow, key)
    assert replay.json()["replayed"] and replay.json()["id"] == first.json()["id"]
    network.assert_not_called()

    async def interrupt():
        async with client.app.state.test_factory() as db, db.begin():
            record = await db.get(WorkflowRun, UUID(first.json()["id"]))
            record.status = "STARTED"
            record.finished_at = None
            record.result = None

    client.portal.call(interrupt)
    with patch("httpx.AsyncClient") as network:
        unknown = start(workflow, key)
    assert unknown.status_code == 202 and unknown.json()["replayed"]
    network.assert_not_called()


def test_document_scope_rbac_acknowledgement_and_disabled_gate(workflow):
    client, pair, _doc, prefix = workflow
    create_member(client, pair, "ANALYST")
    analyst = login(client, "member@example.com")
    assert client.get("/api/v1/workflows/config", headers=headers(analyst)).status_code == 403
    assert (
        client.post(
            prefix + "/workflow-runs",
            headers=headers(analyst),
            json={"request_key": str(uuid4()), "acknowledge_external_request": True},
        ).status_code
        == 403
    )
    assert start(workflow, acknowledge_external_request=False).status_code == 422
    assert start(workflow, source="do not send caller text").status_code == 422
    client.app.state.settings.workflow_enabled = False
    with patch("httpx.AsyncClient") as network:
        disabled = start(workflow)
    assert disabled.status_code == 503
    network.assert_not_called()


def test_foreign_tenant_cannot_read_or_run_source(workflow):
    client, _pair, _doc, prefix = workflow
    beta = register(client, "beta")
    assert client.get(prefix + "/workflow-runs", headers=headers(beta)).status_code == 404
    with patch("httpx.AsyncClient") as network:
        result = client.post(
            prefix + "/workflow-runs",
            headers=headers(beta),
            json={"request_key": str(uuid4()), "acknowledge_external_request": True},
        )
    assert result.status_code == 404
    network.assert_not_called()


def test_model_and_probe_share_admission_budget_and_lease(workflow):
    from test_ai import probe

    client, pair, _doc, _prefix = workflow
    client.app.state.settings.llm_probe_limit = 2
    with model_http():
        assert start(workflow).json()["status"] == "REVIEW_REQUIRED"
    with patch("httpx.AsyncClient") as network:
        assert start(workflow).status_code == 429
        assert probe(client, pair).status_code == 429
    network.assert_not_called()


@pytest.mark.parametrize("change", ["archive", "role", "source"])
def test_source_and_actor_changes_between_steps_fail_closed(workflow, change):
    from app.processing.models import DocumentChunk

    client, _pair, doc, _prefix = workflow

    async def alter(data, is_classification):
        if is_classification:
            async with client.app.state.test_factory() as db, db.begin():
                if change == "archive":
                    document = await db.get(Document, UUID(doc["id"]))
                    document.status = DocumentStatus.ARCHIVED
                if change == "role":
                    actor = await db.scalar(
                        select(User).where(User.email == "admin@alpha.example.com")
                    )
                    actor.role = "VIEWER"
                if change == "source":
                    chunk = await db.scalar(
                        select(DocumentChunk).where(DocumentChunk.document_id == UUID(doc["id"]))
                    )
                    chunk.text = "tampered source"
        return data

    with model_http(alter=alter) as calls:
        response = start(workflow)
    if change == "role":
        assert response.status_code == 403

        async def recorded():
            async with client.app.state.test_factory() as db:
                run = await db.scalar(select(WorkflowRun))
                assert (
                    run.status == "FAILED"
                    and run.error_code == "WORKFLOW_AUTHORIZATION_REVOKED"
                    and run.result is None
                )
                assert len(run.calls) == 1

        client.portal.call(recorded)
    else:
        run = response.json()
        assert run["status"] == "FAILED" and run["result"] is None, response.text
        assert len(run["calls"]) == 1
    assert len(calls) == 1


def test_no_retrieval_hit_does_not_fabricate_context_or_make_second_model_call(workflow):
    client, _pair, _doc, _prefix = workflow
    with (
        patch.object(client.app.state.vector_store, "search", AsyncMock(return_value=[])),
        model_http() as calls,
    ):
        response = start(workflow)
    assert response.json()["error_code"] == "WORKFLOW_CONTEXT_NOT_FOUND" and len(calls) == 1


def test_paid_failure_is_persisted_without_retry_or_zero_cost_claim(workflow):
    _client, _pair, _doc, _prefix = workflow
    key = uuid4()
    original = httpx.AsyncClient
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(429, text="private remote body")

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: original(transport=httpx.MockTransport(fail), **kwargs),
    ):
        response = start(workflow, key)
    assert (
        response.json()["status"] == "FAILED"
        and response.json()["error_code"] == "PROVIDER_RATE_LIMITED"
        and response.json()["calls"] == []
    )
    assert "private remote body" not in response.text and len(calls) == 1
    assert start(workflow, key).json()["replayed"]


def test_state_settings_and_trace_privacy():
    with pytest.raises(ValidationError):
        AgentState(
            organization_id=uuid4(),
            document_id=uuid4(),
            version_id=uuid4(),
            decision="AUTO_VALIDATE",
        )
    with pytest.raises(ValidationError):
        Settings(workflow_enabled=True)
    with pytest.raises(ValidationError):
        Settings(
            workflow_enabled=True,
            llm_enabled=True,
            retrieval_enabled=True,
            llm_api_key=SecretStr("test"),
            workflow_timeout_seconds=30,
        )


def test_ambient_langsmith_tracing_does_not_export_workflow_state(workflow, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "synthetic-not-real")
    with (
        patch("langsmith.client.Client.create_run") as trace,
        patch("requests.sessions.Session.request") as network,
        model_http(),
    ):
        response = start(workflow)
    assert response.json()["status"] == "REVIEW_REQUIRED", response.text
    trace.assert_not_called()
    network.assert_not_called()


@pytest.mark.parametrize("when", ["admission", "refresh"])
def test_workflow_redis_outages_never_fall_back_to_unlimited_provider_calls(workflow, when):
    from redis.exceptions import ConnectionError as RedisConnectionError

    client, _pair, _doc, _prefix = workflow
    client.app.state.redis.eval = AsyncMock(
        side_effect=RedisConnectionError("private redis failure")
        if when == "admission"
        else [1, RedisConnectionError("private redis failure"), 1]
    )
    with patch("httpx.AsyncClient") as network:
        response = start(workflow)
    if when == "admission":
        assert response.status_code == 503
    else:
        assert (
            response.json()["status"] == "FAILED"
            and response.json()["error_code"] == "PROVIDER_LIMITER_UNAVAILABLE"
        )
    assert "private redis failure" not in response.text
    network.assert_not_called()


def test_idempotency_key_cannot_be_rebound_to_another_document(workflow):
    from test_documents import upload

    client, pair, _doc, _prefix = workflow
    key = uuid4()
    with model_http():
        response = start(workflow, key)
    other = upload(client, pair, b"Different synthetic original")
    with patch("httpx.AsyncClient") as network:
        conflict = client.post(
            f"/api/v1/documents/{other['id']}/versions/1/workflow-runs",
            headers=headers(pair),
            json={"request_key": str(key), "acknowledge_external_request": True},
        )
    assert response.json()["status"] == "REVIEW_REQUIRED" and conflict.status_code == 409
    network.assert_not_called()
    beta = register(client, "beta")
    assert (
        client.get(
            "/api/v1/workflow-runs/" + response.json()["id"], headers=headers(beta)
        ).status_code
        == 404
    )


def test_sql_tenant_foreign_keys_reject_cross_org_workflow_rows(workflow):
    from sqlalchemy.exc import IntegrityError

    from app.auth.models import AuthSession
    from app.documents.models import DocumentVersion

    client, _pair, doc, _prefix = workflow
    register(client, "beta")

    async def insert():
        async with client.app.state.test_factory() as db, db.begin():
            actor = await db.scalar(select(User).where(User.email == "admin@beta.example.com"))
            session = await db.scalar(select(AuthSession).where(AuthSession.user_id == actor.id))
            version = await db.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == UUID(doc["id"]))
            )
            db.add(
                WorkflowRun(
                    organization_id=actor.organization_id,
                    requested_by=actor.id,
                    auth_session_id=session.id,
                    document_id=UUID(doc["id"]),
                    version_id=version.id,
                    request_key=uuid4(),
                    revision="test",
                    source_checksum=version.checksum,
                )
            )
            await db.flush()

    with pytest.raises(IntegrityError):
        client.portal.call(insert)
