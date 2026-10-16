"""Real PostgreSQL, Redis, MinIO, Qdrant and LangGraph. ONLY model HTTP is mocked."""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from qdrant_client import models
from sqlalchemy import delete, select
from test_auth import headers, register
from test_extraction import TEXT as LEASE_TEXT
from test_extraction import lease_model
from test_workflows import TEXT, model_http

from app.auth.models import Organization
from app.conflicts.models import ConflictComparison
from app.core.config import Settings
from app.db.session import create_engine
from app.documents.models import Document, DocumentMetadata, DocumentVersion
from app.documents.storage import S3ObjectStore
from app.governance.models import ReviewDecision, ReviewTask
from app.main import create_app
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.worker import run_once as parse_once
from app.rules.models import RuleEvaluation
from app.search.models import IndexJob
from app.search.store import exact
from app.search.worker import run_once as index_once
from app.workflows.models import WorkflowRun

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires migrated PostgreSQL/Redis/MinIO/Qdrant; LLM remains explicitly mocked",
)


@pytest.mark.parametrize("mode", ["CANDIDATES", "LEASE_FIELDS"])
def test_real_infrastructure_workflow_ledger_shared_budget_and_tenant_isolation(mode):
    settings = Settings(
        auth_rate_limit=1000,
        processing_enabled=True,
        allow_unscanned_processing=True,
        retrieval_enabled=True,
        llm_enabled=True,
        llm_api_key=SecretStr("synthetic-not-real"),
        workflow_enabled=True,
        llm_probe_limit=4 if mode == "LEASE_FIELDS" else 2,
        conflicts_enabled=mode == "LEASE_FIELDS",
        lease_extraction_enabled=mode == "LEASE_FIELDS",
        rules_enabled=mode == "LEASE_FIELDS",
        llm_max_output_tokens=2048 if mode == "LEASE_FIELDS" else 512,
    )
    slug = "workflow-ci-" + uuid4().hex
    store = S3ObjectStore(settings)
    org_ids = []
    with TestClient(create_app(settings)) as client:
        vectors = client.app.state.vector_store
        try:
            pair = register(client, slug)
            beta = register(client, slug + "b")
            for current in (pair, beta):
                org_ids.append(
                    client.get("/api/v1/organizations/current", headers=headers(current)).json()[
                        "id"
                    ]
                )
            document = client.post(
                "/api/v1/documents",
                headers=headers(pair),
                files={
                    "file": (
                        "synthetic.txt",
                        LEASE_TEXT if mode == "LEASE_FIELDS" else TEXT,
                        "text/plain",
                    )
                },
            )
            assert document.status_code == 201, document.text
            prefix = f"/api/v1/documents/{document.json()['id']}/versions/1"
            assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
            assert client.portal.call(parse_once, client.app.state.session_factory, store, settings)
            assert client.post(prefix + "/index", headers=headers(pair)).status_code == 202
            assert client.portal.call(
                index_once, client.app.state.session_factory, vectors, settings
            )
            budget_prefix = prefix
            key = str(uuid4())
            body = {
                "request_key": key,
                "acknowledge_external_request": True,
                "extraction_mode": mode,
            }
            with lease_model() if mode == "LEASE_FIELDS" else model_http() as calls:
                result = client.post(prefix + "/workflow-runs", headers=headers(pair), json=body)
            assert result.status_code == 200 and result.json()["status"] == "REVIEW_REQUIRED", (
                result.text
            )
            assert len(calls) == 2
            if mode == "LEASE_FIELDS":
                assert len(result.json()["result"]["lease_extraction"]["fields"]) == 12
                assert result.json()["result"]["rule_report"]["outcome"] == "INDETERMINATE"
                evaluation = client.post(
                    "/api/v1/workflow-runs/" + result.json()["id"] + "/rule-evaluations",
                    headers=headers(pair),
                    json={"request_key": str(uuid4())},
                )
                assert evaluation.status_code == 200, evaluation.text
                uploaded = client.post(
                    f"/api/v1/documents/{document.json()['id']}/versions",
                    headers=headers(pair),
                    files={
                        "file": (
                            "v2.txt",
                            LEASE_TEXT + b"\nSynthetic second uploaded version.",
                            "text/plain",
                        )
                    },
                    data={"expected_version": "1"},
                )
                assert uploaded.status_code == 201, uploaded.text
                budget_prefix = f"/api/v1/documents/{document.json()['id']}/versions/2"
                assert (
                    client.post(budget_prefix + "/process", headers=headers(pair)).status_code
                    == 202
                )
                assert client.portal.call(
                    parse_once, client.app.state.session_factory, store, settings
                )
                assert (
                    client.post(budget_prefix + "/index", headers=headers(pair)).status_code == 202
                )
                assert client.portal.call(
                    index_once, client.app.state.session_factory, vectors, settings
                )
                with lease_model() as second_calls:
                    second = client.post(
                        budget_prefix + "/workflow-runs",
                        headers=headers(pair),
                        json={**body, "request_key": str(uuid4())},
                    )
                assert second.status_code == 200 and second.json()["status"] == "REVIEW_REQUIRED", (
                    second.text
                )
                assert len(second_calls) == 2
                comparison = client.post(
                    "/api/v1/conflict-comparisons",
                    headers=headers(pair),
                    json={
                        "request_key": str(uuid4()),
                        "left_run_id": result.json()["id"],
                        "right_run_id": second.json()["id"],
                    },
                )
                assert comparison.status_code == 200, comparison.text
                assert comparison.json()["report"]["relationship"] == "DOCUMENT_VERSIONS"
                assert comparison.json()["report"]["authority_status"] == "NOT_ESTABLISHED"

            assert client.post(prefix + "/workflow-runs", headers=headers(pair), json=body).json()[
                "replayed"
            ]
            assert (
                client.get(
                    "/api/v1/workflow-runs/" + result.json()["id"], headers=headers(beta)
                ).status_code
                == 404
            )
            assert client.get(prefix + "/workflow-runs", headers=headers(beta)).status_code == 404
            assert (
                client.post(
                    budget_prefix + "/workflow-runs",
                    headers=headers(pair),
                    json={**body, "request_key": str(uuid4())},
                ).status_code
                == 429
            )
            assert (
                client.post(
                    "/api/v1/ai/probes",
                    headers=headers(pair),
                    json={"request_key": str(uuid4()), "acknowledge_external_request": True},
                ).status_code
                == 429
            )
        finally:

            async def clean_vectors():
                if await vectors.client.collection_exists(vectors.collection):
                    for org_id in org_ids:
                        await vectors.client.delete(
                            vectors.collection,
                            models.FilterSelector(
                                filter=models.Filter(must=[exact("organization_id", org_id)])
                            ),
                            wait=True,
                        )

            client.portal.call(clean_vectors)

            async def cleanup():
                engine = create_engine(settings)
                try:
                    async with engine.begin() as connection:
                        organizations = select(Organization.id).where(
                            Organization.slug.in_([slug, slug + "b"])
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
                            ReviewDecision,
                            ReviewTask,
                            ConflictComparison,
                            RuleEvaluation,
                            WorkflowRun,
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
