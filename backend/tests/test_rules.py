"""Deterministic engine tests and real SQLite/graph/API, with explicit model HTTP mocks only."""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_auth import create_member, headers, login, register
from test_extraction import lease_model, lease_workflow, start  # noqa: F401 - register shared pytest fixture

from app.core.config import Settings
from app.extraction.schemas import GROUPS, LeaseExtraction
from app.extraction.validation import validate_lease_fields
from app.rules.catalog import RULE_IDS, policy
from app.rules.engine import annotate, evaluate, input_hash
from app.search.router import Citation

NOW = datetime(2026, 10, 16, tzinfo=UTC)


@pytest.fixture
def rules_case(request):
    return request.getfixturevalue("lease_workflow")


def sample(rows):
    org_doc = uuid4()
    version = uuid4()
    chunk = uuid4()
    quotes = [
        quote or f"{group}.{field} #{i}: {value}"
        for i, (group, field, value, interpretation, quote) in enumerate(rows)
    ]
    text = "\n".join(quotes)
    source = Citation(
        chunk_id=chunk,
        document_id=org_doc,
        document_title="Synthetic",
        version_id=version,
        version_number=1,
        page=1,
        page_number_kind="LOGICAL",
        section=None,
        start_offset=0,
        end_offset=len(text),
        evidence=text,
        parser_revision="test",
        document_type="LEASE",
        property_name=None,
        original_url=f"/api/v1/documents/{org_doc}/versions/1/download",
    )
    data = {group: [] for group in GROUPS}
    for (group, field, value, interpretation, _quote), quote in zip(rows, quotes, strict=True):
        data[group].append(
            {
                "field": field,
                "value": value,
                "interpretation": interpretation,
                "quote": quote,
                "chunk_id": str(chunk),
            }
        )
    return validate_lease_fields(LeaseExtraction.model_validate(data), [source])


def outcome(report, key):
    return [item.outcome for item in report.findings if item.rule_id == key]


def test_deterministic_catalog_snapshots_hashes_and_no_input_mutation():
    lease = sample([("renewals", "duration", "5 years", "EXPLICIT", "Renewal duration: 5 years")])
    original = lease.model_dump()
    current = policy()
    a = evaluate(lease, current, NOW)
    b = evaluate(lease, current, NOW)
    assert a == b and lease.model_dump() == original
    assert outcome(a, "LG-001") == ["PASS"] and outcome(a, "LG-008") == ["PASS"]
    updated = annotate(lease, a)
    assert input_hash(updated) == input_hash(lease) and updated.fields[0].confidence is None
    assert (
        updated.fields[0].status == "MODEL_PROPOSED_UNVERIFIED"
        and a.decision == "REVIEW_REQUIRED"
        and a.legal_compliance_established is False
    )
    assert all(
        rule.version == "1.0.0"
        and rule.conditions
        and rule.actions
        and rule.created_at
        and rule.updated_at
        for rule in current.rules
    )


@pytest.mark.parametrize(
    "value,expected",
    [("0 days", "FAIL"), ("30 days", "PASS"), ("30 business days", "INDETERMINATE")],
)
def test_positive_notice_duration_is_a_literal_check_not_a_legal_threshold(value, expected):
    lease = sample([("termination", "notice_period", value, "EXPLICIT", None)])
    assert outcome(evaluate(lease, policy(), NOW), "LG-004") == [expected]


@pytest.mark.parametrize(
    "start_date,end_date,expected",
    [
        ("2026-01-01", "2027-01-01", "PASS"),
        ("2027-01-01", "2026-01-01", "FAIL"),
        ("01/02/2026", "2027-01-01", "INDETERMINATE"),
    ],
)
def test_date_order_does_not_guess_locale_or_reconcile_multiple_candidates(
    start_date, end_date, expected
):
    lease = sample(
        [
            ("dates", "commencement", start_date, "EXPLICIT", None),
            ("dates", "expiration", end_date, "EXPLICIT", None),
        ]
    )
    assert outcome(evaluate(lease, policy(), NOW), "LG-006") == [expected]
    multiple = sample(
        [
            ("dates", "commencement", "2026-01-01", "EXPLICIT", None),
            ("dates", "commencement", "2026-02-01", "EXPLICIT", None),
            ("dates", "expiration", "2027-01-01", "EXPLICIT", None),
        ]
    )
    assert outcome(evaluate(multiple, policy(), NOW), "LG-006") == ["INDETERMINATE"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("USD 5000.00", "PASS"),
        ("USD 5000.001", "FAIL"),
        ("JPY 5000.5", "FAIL"),
        ("JPY 5000.00", "PASS"),
        ("USD TBD", "INDETERMINATE"),
    ],
)
def test_reference_money_scale_never_rounds_or_changes_source_value(value, expected):
    lease = sample([("rent", "base_rent", value, "EXPLICIT", None)])
    assert outcome(evaluate(lease, policy(), NOW), "LG-005") == [expected]
    assert lease.fields[0].value == value


def test_missing_termination_notice_is_unknown_in_partial_context_not_absent():
    lease = sample([("termination", "right", "Tenant may terminate", "EXPLICIT", None)])
    report = evaluate(lease, policy(), NOW)
    assert outcome(report, "LG-007") == ["INDETERMINATE"]
    assert any(item.reason == "TERMINATION_NOTICE_NOT_OBSERVED" for item in report.findings)
    assert evaluate(sample([]), policy(), NOW).outcome == "INDETERMINATE"


@pytest.mark.parametrize("interpretation", ["AMBIGUOUS", "CONDITIONAL", "CROSS_REFERENCE"])
def test_uncertain_interpretation_cannot_pass_explicitness_rule(interpretation):
    report = evaluate(
        sample([("renewals", "duration", "5 years", interpretation, "Renewal duration: 5 years")]),
        policy(),
        NOW,
    )
    assert outcome(report, "LG-002") == ["INDETERMINATE"] and outcome(report, "LG-008") == [
        "INDETERMINATE"
    ]


def test_tampered_offsets_or_normalization_cannot_pass_integrity_checks():
    lease = sample([("rent", "base_rent", "USD 5000", "EXPLICIT", None)])
    corrupt = lease.model_copy(
        update={"fields": [lease.fields[0].model_copy(update={"quote_start": 1})]}
    )
    assert outcome(evaluate(corrupt, policy(), NOW), "LG-001") == ["FAIL"]
    normalized = lease.fields[0].normalized.model_copy(update={"amount": "9999"})
    corrupt = lease.model_copy(
        update={"fields": [lease.fields[0].model_copy(update={"normalized": normalized})]}
    )
    assert outcome(evaluate(corrupt, policy(), NOW), "LG-003") == ["FAIL"]


def test_disabled_rules_are_visible_not_passes_and_unknown_policies_rejected():
    lease = sample([("notices", "period", "0 days", "EXPLICIT", None)])
    assert evaluate(lease, policy(disabled=RULE_IDS), NOW).outcome == "DISABLED"
    assert evaluate(lease, policy(False), NOW).outcome == "DISABLED"
    partial = evaluate(lease, policy(disabled=("LG-004",)), NOW)
    assert outcome(partial, "LG-004") == ["DISABLED"]
    assert partial.policy_sha256 != evaluate(lease, policy(), NOW).policy_sha256
    with pytest.raises(ValidationError):
        Settings(rules_disabled=("__import__('os')",))
    with pytest.raises(ValidationError):
        Settings(rules_disabled=("LG-001", "LG-001"))
    altered = policy().model_copy(
        update={
            "rules": tuple(rule.model_copy(update={"version": "9.0.0"}) for rule in policy().rules)
        }
    )
    with pytest.raises(ValueError, match="snapshot"):
        evaluate(lease, altered, NOW)


def test_graph_rules_add_no_provider_calls_and_remain_review_required(rules_case):
    client, _pair, _doc, _prefix = rules_case
    client.app.state.settings.rules_enabled = True
    with lease_model() as calls:
        response = start(rules_case)
    run = response.json()
    assert run["status"] == "REVIEW_REQUIRED", response.text
    assert run["revision"] == "lease-field-agent-v2" and len(calls) == 2
    assert "run_rules" in [item["name"] for item in run["trace"]]
    report = run["result"]["rule_report"]
    assert report["outcome"] == "INDETERMINATE" and report["legal_compliance_established"] is False
    assert all(
        field["confidence"] is None and field["status"] == "MODEL_PROPOSED_UNVERIFIED"
        for field in run["result"]["lease_extraction"]["fields"]
    )


def test_local_evaluation_append_only_replay_rbac_and_tenant_scope(rules_case):
    client, pair, _doc, _prefix = rules_case
    with lease_model():
        run = start(rules_case).json()
    client.app.state.settings.rules_enabled = True
    url = f"/api/v1/workflow-runs/{run['id']}/rule-evaluations"
    key = str(uuid4())
    with patch("httpx.AsyncClient") as network:
        first = client.post(url, headers=headers(pair), json={"request_key": key})
        assert first.status_code == 200, first.text
        replay = client.post(url, headers=headers(pair), json={"request_key": key})
    network.assert_not_called()
    assert replay.json()["replayed"] and first.json()["id"] == replay.json()["id"]
    after = client.get("/api/v1/workflow-runs/" + run["id"], headers=headers(pair)).json()
    assert after["result"] == run["result"]  # A new evaluation never rewrites old extraction JSON.
    client.app.state.settings.rules_disabled = ("LG-004",)
    second = client.post(url, headers=headers(pair), json={"request_key": str(uuid4())})
    assert second.json()["policy_sha256"] != first.json()["policy_sha256"]
    assert len(client.get(url, headers=headers(pair)).json()) == 2
    beta = register(client, "beta")
    assert client.get(url, headers=headers(beta)).status_code == 404
    assert (
        client.get(
            "/api/v1/rule-evaluations/" + first.json()["id"], headers=headers(beta)
        ).status_code
        == 404
    )
    create_member(client, pair, "ANALYST")
    analyst = login(client, "member@example.com")
    assert client.get("/api/v1/rules/catalog", headers=headers(analyst)).status_code == 403
    assert (
        client.post(url, headers=headers(analyst), json={"request_key": str(uuid4())}).status_code
        == 403
    )
    assert (
        client.post(
            url, headers=headers(pair), json={"request_key": str(uuid4()), "rules": ["eval"]}
        ).status_code
        == 422
    )
    client.app.state.settings.rules_enabled = False
    assert (
        client.post(url, headers=headers(pair), json={"request_key": str(uuid4())}).status_code
        == 503
    )
    assert client.post(url, headers=headers(pair), json={"request_key": key}).json()["replayed"]


def test_local_evaluation_rejects_archived_source(rules_case):
    from app.documents.models import Document, DocumentStatus

    client, pair, doc, _prefix = rules_case
    with lease_model():
        run = start(rules_case).json()
    client.app.state.settings.rules_enabled = True

    async def archive():
        async with client.app.state.test_factory() as db, db.begin():
            item = await db.get(Document, UUID(doc["id"]))
            item.status = DocumentStatus.ARCHIVED

    client.portal.call(archive)
    response = client.post(
        f"/api/v1/workflow-runs/{run['id']}/rule-evaluations",
        headers=headers(pair),
        json={"request_key": str(uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "change", ["checksum", "current_version", "processing_status", "citation_title"]
)
def test_local_evaluation_rejects_changed_source_contract(rules_case, change):
    from app.documents.models import Document, DocumentVersion

    client, pair, doc, _prefix = rules_case
    with lease_model():
        run = start(rules_case).json()
    client.app.state.settings.rules_enabled = True

    async def changed():
        async with client.app.state.test_factory() as db, db.begin():
            document = await db.get(Document, UUID(doc["id"]))
            version = await db.get(DocumentVersion, UUID(run["version_id"]))
            if change == "checksum":
                version.checksum = "f" * 64
            if change == "current_version":
                document.current_version = 2
            if change == "processing_status":
                version.processing_status = "FAILED"
            if change == "citation_title":
                document.title = "Changed synthetic title"

    client.portal.call(changed)
    response = client.post(
        f"/api/v1/workflow-runs/{run['id']}/rule-evaluations",
        headers=headers(pair),
        json={"request_key": str(uuid4())},
    )
    assert response.status_code == 409, response.text


def test_local_evaluation_key_conflict_and_database_parent_guards(rules_case):
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.rules.models import RuleEvaluation

    client, pair, _doc, _prefix = rules_case
    with lease_model():
        run = start(rules_case).json()
    with lease_model():
        other = start(rules_case).json()
    client.app.state.settings.rules_enabled = True
    key = str(uuid4())
    first = client.post(
        f"/api/v1/workflow-runs/{run['id']}/rule-evaluations",
        headers=headers(pair),
        json={"request_key": key},
    )
    assert first.status_code == 200, first.text
    conflict = client.post(
        f"/api/v1/workflow-runs/{other['id']}/rule-evaluations",
        headers=headers(pair),
        json={"request_key": key},
    )
    assert conflict.status_code == 409, conflict.text

    async def guards():
        table = RuleEvaluation.__table__
        async with client.app.state.test_factory() as db, db.begin():
            record = (await db.execute(select(table))).mappings().one()
            for column in (
                "organization_id",
                "document_id",
                "version_id",
                "workflow_run_id",
                "requested_by",
                "auth_session_id",
            ):
                values = {**record, "id": uuid4(), "request_key": uuid4(), column: uuid4()}
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(table.insert().values(**values))

    client.portal.call(guards)


def test_local_evaluation_rechecks_authorization_before_publication(rules_case):
    from sqlalchemy import update

    from app.auth.models import User
    from app.rules.router import citation

    client, pair, _doc, _prefix = rules_case
    with lease_model():
        run = start(rules_case).json()
    client.app.state.settings.rules_enabled = True

    async def revoke(db, org, chunk):
        result = await citation(db, org, chunk)
        await db.execute(update(User).where(User.organization_id == org).values(role="ANALYST"))
        return result

    with patch("app.rules.router.citation", side_effect=revoke):
        response = client.post(
            f"/api/v1/workflow-runs/{run['id']}/rule-evaluations",
            headers=headers(pair),
            json={"request_key": str(uuid4())},
        )
    assert response.status_code == 403, response.text
