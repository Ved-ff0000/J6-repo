"""Real graph/parsers/SQLite/local Qdrant; provider responses explicitly mocked, not live AI evaluation."""

import json
from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from qdrant_client import AsyncQdrantClient
from test_ai import TestLimiter, reply
from test_auth import headers, register
from test_search import index_work, prepare

from app.ai.openai import strict_schema
from app.core.config import Settings
from app.extraction.normalization import normalize
from app.extraction.schemas import GROUPS, LeaseExtraction, Proposal
from app.search.store import VectorStore

EXAMPLES = {
    "parties": ("landlord", "Acme Estates Ltd.", "Landlord: Acme Estates Ltd.", "EXPLICIT"),
    "dates": ("commencement", "2026-01-01", "Commencement: 2026-01-01.", "EXPLICIT"),
    "rent": (
        "base_rent",
        "USD 5,000.00 per month",
        "Base rent: USD 5,000.00 per month.",
        "EXPLICIT",
    ),
    "renewals": ("duration", "5 years", "Renewal duration: 5 years.", "EXPLICIT"),
    "termination": (
        "right",
        "Tenant may terminate after year three.",
        "Termination: Tenant may terminate after year three.",
        "CONDITIONAL",
    ),
    "notices": ("period", "90 days", "Notice period: 90 days.", "EXPLICIT"),
    "obligations": (
        "responsibility",
        "Tenant must pay utilities.",
        "Obligation: Tenant must pay utilities.",
        "EXPLICIT",
    ),
    "insurance": ("limit", "USD 1,000,000", "Insurance limit: USD 1,000,000.", "EXPLICIT"),
    "maintenance": (
        "responsibility",
        "Landlord repairs structural roof defects.",
        "Maintenance: Landlord repairs structural roof defects.",
        "EXPLICIT",
    ),
    "assignment": (
        "consent",
        "Prior written landlord consent is required.",
        "Assignment: Prior written landlord consent is required.",
        "EXPLICIT",
    ),
    "cam": ("charge", "USD 500 per month", "CAM charge: USD 500 per month.", "EXPLICIT"),
    "escalation": ("rate", "3%", "Escalation rate: 3%.", "EXPLICIT"),
}
TEXT = ("COMMERCIAL LEASE\n" + "\n".join(item[2] for item in EXAMPLES.values())).encode()
pytestmark = pytest.mark.filterwarnings("ignore:Payload indexes have no effect in the local Qdrant")


@pytest.fixture
def lease_workflow(client, processing):
    settings = client.app.state.settings
    settings.retrieval_enabled = True
    settings.llm_enabled = True
    settings.llm_api_key = SecretStr("synthetic-key-not-real")
    settings.workflow_enabled = True
    settings.lease_extraction_enabled = True
    settings.llm_max_output_tokens = 2048
    settings.llm_probe_limit = 20
    client.app.state.session_factory = client.app.state.test_factory
    client.app.state.redis = TestLimiter()
    vectors = VectorStore(settings, AsyncQdrantClient(":memory:"))
    client.portal.call(client.app.state.vector_store.close)
    client.app.state.vector_store = vectors
    pair = register(client)
    doc, prefix = prepare(client, processing, pair, TEXT)
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 202
    assert index_work(client, vectors)
    return client, pair, doc, prefix


def response_fields(chunk_id):
    return {
        group: [
            {
                "field": field,
                "value": value,
                "chunk_id": chunk_id,
                "quote": quote,
                "interpretation": interpretation,
            }
        ]
        for group, (field, value, quote, interpretation) in EXAMPLES.items()
    }


@contextmanager
def lease_model(kind="LEASE", alter=None):
    original = httpx.AsyncClient
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        sources = json.loads(body["messages"][-1]["content"].split("SOURCE_JSON:\n", 1)[1])
        classification = "kind" in body["response_format"]["json_schema"]["schema"]["properties"]
        data = {"kind": kind} if classification else response_fields(sources[0]["chunk_id"])
        if alter and not classification:
            data = alter(data)
        return httpx.Response(200, json=reply(json.dumps(data)))

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    ):
        yield calls


def start(case, key=None, mode="LEASE_FIELDS", **extra):
    client, pair, _doc, prefix = case
    return client.post(
        prefix + "/workflow-runs",
        headers=headers(pair),
        json={
            "request_key": str(key or uuid4()),
            "acknowledge_external_request": True,
            "extraction_mode": mode,
            **extra,
        },
    )


@pytest.mark.parametrize("kind", ["LEASE", "AMENDMENT"])
def test_all_twelve_groups_are_typed_persisted_source_backed_and_unverified(lease_workflow, kind):
    client, pair, doc, prefix = lease_workflow
    with lease_model(kind) as calls:
        response = start(lease_workflow)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["status"] == "REVIEW_REQUIRED", response.text
    assert run["extraction_mode"] == "LEASE_FIELDS" and run["revision"] == "lease-field-agent-v1"
    result = run["result"]
    lease = result["lease_extraction"]
    assert result["scope"] == "LEASE_FIELD_PROPOSALS" and result["review_queue_created"] is False
    assert len(calls) == 2 and run["calls"][1]["prompt_name"] == "workflow_lease_extraction"
    assert "extract_lease_fields" in [item["name"] for item in run["trace"]]
    assert len(lease["fields"]) == 12 and {item["group"] for item in lease["groups"]} == set(GROUPS)
    assert (
        lease["document_completeness"] == "NOT_ESTABLISHED"
        and lease["authoritative_lease_created"] is False
    )
    page = client.get(prefix + "/pages", headers=headers(pair)).json()[0]["text"]
    for field in lease["fields"]:
        assert page[field["quote_start"] : field["quote_end"]] == field["evidence_text"]
        assert page[field["value_start"] : field["value_end"]] == field["value"]
        assert (
            field["citation"]["document_id"] == doc["id"]
            and field["citation"]["version_number"] == 1
        )
        assert field["status"] == "MODEL_PROPOSED_UNVERIFIED" and field["confidence"] is None
        assert field["rule_validation"] == {"status": "NOT_RUN", "passed": None, "rules": []}
    money = next(item for item in lease["fields"] if item["group"] == "rent")
    assert money["normalized"] == {
        "kind": "money",
        "amount": "5000.00",
        "currency": "USD",
        "period": "month",
    }
    assert (
        client.get(
            prefix + "/workflow-runs",
            headers=headers(pair),
            params={"extraction_mode": "LEASE_FIELDS"},
        ).json()[0]["id"]
        == run["id"]
    )
    assert (
        client.get(
            prefix + "/workflow-runs",
            headers=headers(pair),
            params={"extraction_mode": "CANDIDATES"},
        ).json()
        == []
    )
    assert "synthetic-key-not-real" not in response.text


def test_empty_groups_preserve_unknown_coverage_without_asserting_absence(lease_workflow):
    with lease_model(alter=lambda data: {group: [] for group in GROUPS}):
        response = start(lease_workflow)
    lease = response.json()["result"]["lease_extraction"]
    assert lease["fields"] == [] and all(
        group["observation"] == "NOT_OBSERVED_IN_CONTEXT" for group in lease["groups"]
    )
    assert response.json()["result"]["confidence"]["evidence_coverage"] is None


@pytest.mark.parametrize(
    "attack",
    [
        "foreign_chunk",
        "absent_quote",
        "invented_value",
        "wrong_group_field",
        "confidence",
        "normalized",
        "duplicate",
        "missing_group",
        "null_value",
    ],
)
def test_untrusted_model_output_cannot_bypass_typed_evidence_boundaries(lease_workflow, attack):
    def alter(data):
        if attack == "foreign_chunk":
            data["rent"][0]["chunk_id"] = str(uuid4())
        if attack == "absent_quote":
            data["rent"][0]["quote"] = "USD 5,000.00 per month invented text"
        if attack == "invented_value":
            data["rent"][0]["value"] = "USD 10,000"
        if attack == "wrong_group_field":
            data["parties"][0]["field"] = "commencement"
        if attack == "confidence":
            data["rent"][0]["confidence"] = 1
        if attack == "normalized":
            data["rent"][0]["normalized"] = {"amount": "100000", "currency": "USD"}
        if attack == "duplicate":
            data["rent"].append(data["rent"][0])
        if attack == "missing_group":
            del data["escalation"]
        if attack == "null_value":
            data["rent"][0]["value"] = None
        return data

    with lease_model(alter=alter):
        response = start(lease_workflow)
    run = response.json()
    assert run["status"] == "FAILED" and run["result"] is None, response.text
    assert run["error_code"] in {
        "LEASE_EVIDENCE_INVALID",
        "LEASE_DUPLICATE_FIELD",
        "PROVIDER_SCHEMA_INVALID",
    }


@pytest.mark.parametrize("interpretation", ["AMBIGUOUS", "CONDITIONAL", "CROSS_REFERENCE"])
def test_interpretation_is_preserved_and_not_normalized_into_a_fact(lease_workflow, interpretation):
    def alter(data):
        data["rent"][0]["interpretation"] = interpretation
        return data

    with lease_model(alter=alter):
        response = start(lease_workflow)
    assert response.json()["status"] == "REVIEW_REQUIRED", response.text
    field = next(
        item
        for item in response.json()["result"]["lease_extraction"]["fields"]
        if item["group"] == "rent"
    )
    assert (
        field["value"] == "USD 5,000.00 per month"
        and field["interpretation"] == interpretation
        and field["normalized"] is None
        and field["normalization_status"] == "REQUIRES_REVIEW"
    )


def test_mode_is_credential_gated_and_bound_to_idempotency_key(lease_workflow):
    client, pair, _doc, prefix = lease_workflow
    key = uuid4()
    with lease_model() as calls:
        first = start(lease_workflow, key)
    assert len(calls) == 2
    with patch("httpx.AsyncClient") as network:
        assert start(lease_workflow, key).json()["replayed"]
        assert start(lease_workflow, key, mode="CANDIDATES").status_code == 409
        client.app.state.settings.lease_extraction_enabled = False
        assert start(lease_workflow).status_code == 503
        assert start(lease_workflow, key).json()["id"] == first.json()["id"]
    network.assert_not_called()
    assert (
        client.get(prefix + "/workflow-runs", headers=headers(pair)).json()[0]["extraction_mode"]
        == "LEASE_FIELDS"
    )


def test_schema_catalog_is_closed_complete_and_tenant_admin_only(lease_workflow):
    client, pair, _doc, _prefix = lease_workflow
    catalog = client.get("/api/v1/lease-extractions/schema", headers=headers(pair))
    assert catalog.status_code == 200, catalog.text
    schema = catalog.json()["proposal_schema"]
    assert (
        set(schema["properties"]) == set(GROUPS)
        and set(schema["required"]) == set(GROUPS)
        and schema["additionalProperties"] is False
    )
    assert len(json.dumps(strict_schema(LeaseExtraction))) < 32768
    assert client.get("/api/v1/lease-extractions/schema").status_code == 401


@pytest.mark.parametrize(
    "group,field,value,expected",
    [
        ("dates", "commencement", "2024-02-29", {"kind": "date", "iso_date": "2024-02-29"}),
        ("dates", "expiration", "2025-02-29", None),
        ("dates", "commencement", "04/05/2026", None),
        (
            "rent",
            "base_rent",
            "USD 5,000.25 per month",
            {"kind": "money", "amount": "5000.25", "currency": "USD", "period": "month"},
        ),
        (
            "rent",
            "base_rent",
            "INR 100000 per year",
            {"kind": "money", "amount": "100000", "currency": "INR", "period": "year"},
        ),
        (
            "rent",
            "base_rent",
            "USD 0",
            {"kind": "money", "amount": "0", "currency": "USD", "period": None},
        ),
        ("rent", "base_rent", "$5,000", None),
        ("rent", "base_rent", "USD 1,23.45", None),
        ("rent", "base_rent", "USD 5e3", None),
        ("rent", "base_rent", "5000 dollars", None),
        ("rent", "base_rent", "USD 5000 per month or USD 6000 per year", None),
        ("notices", "period", "90 days", {"kind": "duration", "amount": "90", "unit": "days"}),
        (
            "renewals",
            "duration",
            "1.5 years",
            {"kind": "duration", "amount": "1.5", "unit": "years"},
        ),
        ("renewals", "duration", "five years", None),
        ("notices", "period", "30 business days", None),
        ("escalation", "rate", "3.5%", {"kind": "percentage", "percent": "3.5"}),
        ("escalation", "rate", "CPI plus 3%", None),
    ],
)
def test_normalization_never_guesses_locale_currency_deadlines_or_conditional_amounts(
    group, field, value, expected
):
    proposal = Proposal(field=field, value=value, quote=value, chunk_id=uuid4())
    normalized, status = normalize(group, proposal)
    assert (normalized.model_dump() if normalized else None) == expected
    assert status == ("NORMALIZED" if expected else "REQUIRES_REVIEW")


def test_group_and_total_caps_and_safe_configuration():
    with pytest.raises(ValidationError):
        Settings(lease_extraction_enabled=True)
    with pytest.raises(ValidationError):
        LeaseExtraction(
            parties=[{"field": "landlord", "value": " ", "quote": " ", "chunk_id": str(uuid4())}]
        )
    data = response_fields(str(uuid4()))
    for group in GROUPS:
        data[group] *= 3
    with pytest.raises(ValidationError, match="24 total"):
        LeaseExtraction.model_validate(data)
    data = response_fields(str(uuid4()))
    data["rent"] *= 7
    with pytest.raises(ValidationError):
        LeaseExtraction.model_validate(data)


def test_unknown_classification_does_not_claim_empty_lease_extraction(lease_workflow):
    with lease_model(kind="UNKNOWN") as calls:
        response = start(lease_workflow)
    assert response.json()["status"] == "REVIEW_REQUIRED" and len(calls) == 1
    assert response.json()["result"]["lease_extraction"] is None
