"""Pure comparison contracts plus real parsers/SQLite/API. All model HTTP is explicitly mocked."""

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from test_auth import create_member, headers, login, register
from test_extraction import TEXT, lease_model, lease_workflow, start  # noqa: F401
from test_processing import work
from test_rules import sample
from test_search import index_work, prepare

from app.conflicts.engine import compare
from app.conflicts.schemas import Source

NOW = datetime(2026, 10, 16, tzinfo=UTC)
pytestmark = pytest.mark.filterwarnings("ignore:Payload indexes have no effect in the local Qdrant")


def inputs(left_rows, right_rows, amendment=False):
    left = sample(left_rows)
    right = sample(right_rows)
    doc = uuid4()
    version = uuid4()
    other = uuid4() if amendment else doc
    other_version = uuid4()

    def retarget(extraction, document, ver, number):
        return extraction.model_copy(
            update={
                "fields": [
                    f.model_copy(
                        update={
                            "citation": f.citation.model_copy(
                                update={
                                    "document_id": document,
                                    "version_id": ver,
                                    "version_number": number,
                                    "original_url": f"/api/v1/documents/{document}/versions/{number}/download",
                                }
                            )
                        }
                    )
                    for f in extraction.fields
                ]
            }
        )

    left = retarget(left, doc, version, 1)
    right = retarget(right, other, other_version, 1 if amendment else 2)
    a = Source(
        run_id=uuid4(),
        document_id=doc,
        version_id=version,
        version_number=1,
        checksum="a" * 64,
        version_created_at=NOW,
        parent_document_id=None,
        document_type="LEASE",
    )
    b = Source(
        run_id=uuid4(),
        document_id=other,
        version_id=other_version,
        version_number=1 if amendment else 2,
        checksum="b" * 64,
        version_created_at=NOW,
        parent_document_id=doc if amendment else None,
        document_type="AMENDMENT" if amendment else "LEASE",
    )
    return left, right, a, b


def duration(value):
    return [("renewals", "duration", value, "EXPLICIT", f"Renewal duration: {value}.")]


@pytest.mark.parametrize("amendment", [False, True])
def test_renewal_difference_preserves_both_proposals_without_authority_or_overwrite(amendment):
    args = inputs(duration("3 years"), duration("5 years"), amendment)
    original = [v.model_dump() for v in args]
    report = compare(*args, NOW)
    assert report == compare(*args, NOW)
    assert report.outcome == "POTENTIAL_CONFLICTS_DETECTED"
    assert (
        report.findings[0].left[0].value == "3 years"
        and report.findings[0].right[0].value == "5 years"
    )
    assert (
        report.authority_status == "NOT_ESTABLISHED"
        and report.decision == "REVIEW_REQUIRED"
        and not report.previous_values_overwritten
    )
    assert report.latest_uploaded_version_id == (None if amendment else args[3].version_id)
    assert [v.model_dump() for v in args] == original
    reverse = compare(args[1], args[0], args[3], args[2], NOW)
    assert reverse.latest_uploaded_version_id == report.latest_uploaded_version_id


@pytest.mark.parametrize(
    "left,right,outcome",
    [
        ("3 years", "3 years", "MATCH"),
        ("3 years", "36 months", "INDETERMINATE"),
        ("three years", "5 years", "INDETERMINATE"),
    ],
)
def test_literal_duration_comparison_never_converts_units_or_guesses(left, right, outcome):
    report = compare(*inputs(duration(left), duration(right)), NOW)
    assert report.findings[0].outcome == outcome


@pytest.mark.parametrize(
    "left,right,outcome",
    [
        ("USD 5,000.00 per month", "USD 5000 per month", "MATCH"),
        ("USD 5000 per month", "USD 5000 per year", "INDETERMINATE"),
        ("USD 5000", "EUR 5000", "INDETERMINATE"),
        ("USD 5000", "USD 6000", "POTENTIAL_CONFLICT"),
    ],
)
def test_money_comparison_requires_same_explicit_currency_and_period(left, right, outcome):
    report = compare(
        *inputs(
            [("rent", "base_rent", left, "EXPLICIT", None)],
            [("rent", "base_rent", right, "EXPLICIT", None)],
        ),
        NOW,
    )
    assert report.findings[0].outcome == outcome


def test_ambiguous_multiple_unobserved_and_empty_inputs_never_establish_deletion_or_completeness():
    a, b, sa, sb = inputs(duration("3 years"), [])
    report = compare(a, b, sa, sb, NOW)
    assert report.findings[0].outcome == "NOT_OBSERVED" and not report.completeness_established
    assert compare(*inputs([], []), NOW).outcome == "INDETERMINATE"
    a, b, sa, sb = inputs(duration("3 years") + duration("5 years"), duration("7 years"))
    assert (
        compare(a, b, sa, sb, NOW).findings[0].reason
        == "MULTIPLE_PROPOSALS_NO_UNIQUE_CLAUSE_ALIGNMENT"
    )
    a, b, sa, sb = inputs(
        [("renewals", "duration", "3 years", "CONDITIONAL", None)], duration("5 years")
    )
    assert compare(a, b, sa, sb, NOW).findings[0].outcome == "INDETERMINATE"


def test_invalid_linkage_normalization_and_unrelated_sources_are_not_comparable():
    a, b, sa, sb = inputs(duration("3 years"), duration("5 years"))
    bad = a.model_copy(update={"fields": [a.fields[0].model_copy(update={"value_start": 999})]})
    assert compare(bad, b, sa, sb, NOW).findings[0].reason == "SOURCE_LINK_NOT_ESTABLISHED"
    bad = a.model_copy(update={"fields": [a.fields[0].model_copy(update={"normalized": None})]})
    assert compare(bad, b, sa, sb, NOW).findings[0].reason == "NORMALIZATION_MISMATCH"
    with pytest.raises(ValueError, match="Related"):
        new = uuid4()
        b = b.model_copy(
            update={
                "fields": [
                    f.model_copy(
                        update={"citation": f.citation.model_copy(update={"document_id": new})}
                    )
                    for f in b.fields
                ]
            }
        )
        compare(a, b, sa, sb.model_copy(update={"document_id": new}), NOW)
    with pytest.raises(ValueError, match="Distinct"):
        compare(a, a, sa, sa, NOW)


@pytest.fixture
def comparison_case(request, processing):
    case = request.getfixturevalue("lease_workflow")
    client, pair, doc, _prefix = case
    with lease_model():
        left = start(case)
    assert left.status_code == 200, left.text
    kind = getattr(request, "param", "amendment")
    text = TEXT.replace(b"5 years", b"3 years")
    if kind == "versions":
        uploaded = client.post(
            f"/api/v1/documents/{doc['id']}/versions",
            headers=headers(pair),
            files={"file": ("v2.txt", text, "text/plain")},
            data={"expected_version": "1"},
        )
        assert uploaded.status_code == 201, uploaded.text
        other = uploaded.json()
        prefix = f"/api/v1/documents/{doc['id']}/versions/2"
        assert client.post(prefix + "/process", headers=headers(pair)).status_code == 202
        assert work(client, processing)
    else:
        other, prefix = prepare(
            client, processing, pair, text, document_type="AMENDMENT", parent_document_id=doc["id"]
        )
    assert client.post(prefix + "/index", headers=headers(pair)).status_code == 202
    assert index_work(client, client.app.state.vector_store)

    def alter(data):
        for field in data["renewals"]:
            field["value"] = field["value"].replace("5 years", "3 years")
            field["quote"] = field["quote"].replace("5 years", "3 years")
        return data

    with lease_model("AMENDMENT" if kind != "versions" else "LEASE", alter=alter):
        right = start((client, pair, other, prefix))
    assert right.status_code == 200, right.text
    assert right.json()["result"]["lease_extraction"] is not None, right.text
    return client, pair, left.json(), right.json()


def body(left, right, key=None):
    return {
        "left_run_id": left["id"],
        "right_run_id": right["id"],
        "request_key": str(key or uuid4()),
    }


@pytest.mark.parametrize("comparison_case", ["versions", "amendment"], indirect=True)
def test_local_comparison_no_network_immutable_history_and_disabled_replay(comparison_case):
    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True
    client.app.state.settings.llm_enabled = False
    client.app.state.settings.workflow_enabled = False
    client.app.state.settings.retrieval_enabled = False
    request = body(left, right)
    with patch("httpx.AsyncClient") as network:
        response = client.post("/api/v1/conflict-comparisons", headers=headers(pair), json=request)
    network.assert_not_called()
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert (
        report["outcome"] == "POTENTIAL_CONFLICTS_DETECTED"
        and report["authority_status"] == "NOT_ESTABLISHED"
    )
    for run in (left, right):
        assert (
            client.get("/api/v1/workflow-runs/" + run["id"], headers=headers(pair)).json()["result"]
            == run["result"]
        )
        assert (
            len(
                client.get(
                    f"/api/v1/workflow-runs/{run['id']}/conflict-comparisons", headers=headers(pair)
                ).json()
            )
            == 1
        )
    client.app.state.settings.conflicts_enabled = False
    replay = client.post("/api/v1/conflict-comparisons", headers=headers(pair), json=request)
    assert replay.json()["id"] == response.json()["id"] and replay.json()["replayed"]
    assert (
        client.post(
            "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
        ).status_code
        == 503
    )
    assert (
        client.post(
            "/api/v1/conflict-comparisons",
            headers=headers(pair),
            json={**request, "left_run_id": right["id"], "right_run_id": left["id"]},
        ).status_code
        == 409
    )


def test_comparison_scope_rbac_strict_input_and_archived_counterpart_visibility(comparison_case):
    from app.documents.models import Document, DocumentStatus

    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True
    response = client.post(
        "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
    )
    assert response.status_code == 200, response.text
    url = "/api/v1/conflict-comparisons/" + response.json()["id"]
    beta = register(client, "beta", email="beta@example.com")
    assert client.get(url, headers=headers(beta)).status_code == 404
    assert (
        client.post(
            "/api/v1/conflict-comparisons", headers=headers(beta), json=body(left, right)
        ).status_code
        == 404
    )
    create_member(client, pair, "ANALYST")
    analyst = login(client, "member@example.com")
    assert client.get(url, headers=headers(analyst)).status_code == 403
    assert client.get("/api/v1/conflicts/config", headers=headers(analyst)).status_code == 403
    assert (
        client.post(
            "/api/v1/conflict-comparisons",
            headers=headers(pair),
            json={**body(left, right), "authority": "right"},
        ).status_code
        == 422
    )

    async def archive():
        async with client.app.state.test_factory() as db, db.begin():
            document = await db.get(Document, UUID(left["document_id"]))
            document.status = DocumentStatus.ARCHIVED

    client.portal.call(archive)
    assert client.get(url, headers=headers(pair)).status_code == 404
    history = client.get(
        f"/api/v1/workflow-runs/{right['id']}/conflict-comparisons", headers=headers(pair)
    )
    assert history.status_code == 200 and history.json() == []
    assert (
        client.post(
            "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
        ).status_code
        == 404
    )


@pytest.mark.parametrize("change", ["checksum", "processing_status", "parent", "metadata"])
def test_changed_source_or_relationship_rejects_new_comparison(comparison_case, change):
    from sqlalchemy import select

    from app.documents.models import Document, DocumentMetadata, DocumentVersion

    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True

    async def changed():
        async with client.app.state.test_factory() as db, db.begin():
            if change in {"checksum", "processing_status"}:
                version = await db.get(DocumentVersion, UUID(right["version_id"]))
                if change == "checksum":
                    version.checksum = "f" * 64
                else:
                    version.processing_status = "FAILED"
            elif change == "parent":
                document = await db.get(Document, UUID(right["document_id"]))
                document.parent_document_id = None
            else:
                metadata = await db.scalar(
                    select(DocumentMetadata).where(
                        DocumentMetadata.document_id == UUID(right["document_id"])
                    )
                )
                metadata.document_type = "LEASE"

    client.portal.call(changed)
    response = client.post(
        "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
    )
    assert response.status_code == 409, response.text


def test_comparison_database_parent_guards_and_unsafe_downgrade(comparison_case):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.conflicts.models import ConflictComparison

    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True
    response = client.post(
        "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
    )
    assert response.status_code == 200, response.text
    spec = importlib.util.spec_from_file_location(
        "conflict_migration",
        Path(__file__).parents[1] / "alembic" / "versions" / "0011_conflicts.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    async def guards():
        table = ConflictComparison.__table__
        async with client.app.state.test_factory() as db, db.begin():
            record = (await db.execute(select(table))).mappings().one()
            for column in (
                "organization_id",
                "left_document_id",
                "left_version_id",
                "left_run_id",
                "right_document_id",
                "right_version_id",
                "right_run_id",
                "requested_by",
                "auth_session_id",
            ):
                values = {**record, "id": uuid4(), "request_key": uuid4(), column: uuid4()}
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(table.insert().values(**values))
            values = {
                **record,
                "id": uuid4(),
                "request_key": uuid4(),
                "right_run_id": record["left_run_id"],
                "right_document_id": record["left_document_id"],
                "right_version_id": record["left_version_id"],
            }
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(table.insert().values(**values))

            def downgrade(connection):
                with (
                    Operations.context(MigrationContext.configure(connection)),
                    pytest.raises(RuntimeError, match="Preserve conflict comparisons"),
                ):
                    migration.downgrade()

            connection = await db.connection()
            await connection.run_sync(downgrade)
            assert (await db.scalar(select(ConflictComparison.id))) == UUID(response.json()["id"])

    client.portal.call(guards)


def test_comparison_rechecks_role_before_publication(comparison_case):
    from sqlalchemy import update

    from app.auth.models import User
    from app.conflicts.router import citation

    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True

    async def revoke(db, org, chunk):
        result = await citation(db, org, chunk)
        await db.execute(update(User).where(User.organization_id == org).values(role="ANALYST"))
        return result

    with patch("app.conflicts.router.citation", side_effect=revoke):
        response = client.post(
            "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, right)
        )
    assert response.status_code == 403, response.text


def test_same_version_is_not_amendment_or_version_comparison(comparison_case):
    client, pair, left, right = comparison_case
    client.app.state.settings.conflicts_enabled = True
    prefix = f"/api/v1/documents/{right['document_id']}/versions/1"

    def alter(data):
        for item in data["renewals"]:
            item["quote"] = item["quote"].replace("5 years", "3 years")
            item["value"] = item["value"].replace("5 years", "3 years")
        return data

    with lease_model("AMENDMENT", alter=alter):
        extra = start((client, pair, {}, prefix))
    assert extra.status_code == 200, extra.text
    response = client.post(
        "/api/v1/conflict-comparisons", headers=headers(pair), json=body(right, extra.json())
    )
    assert response.status_code == 409, response.text
    assert (
        client.post(
            "/api/v1/conflict-comparisons", headers=headers(pair), json=body(left, left)
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/conflict-comparisons",
            headers=headers(pair),
            json={**body(left, right), "right_run_id": str(uuid4())},
        ).status_code
        == 404
    )


@pytest.mark.parametrize("kind", ["conflict", "rule"])
def test_unique_key_collision_replays_inside_the_request_transaction(comparison_case, kind):
    """Simulate the pre-insert miss of a concurrent winner, using real DB constraints/savepoints."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.conflicts.models import ConflictComparison
    from app.rules.models import RuleEvaluation

    client, pair, left, right = comparison_case
    settings = client.app.state.settings
    settings.conflicts_enabled = True
    settings.rules_enabled = True
    url = (
        "/api/v1/conflict-comparisons"
        if kind == "conflict"
        else f"/api/v1/workflow-runs/{left['id']}/rule-evaluations"
    )
    request = body(left, right) if kind == "conflict" else {"request_key": str(uuid4())}
    first = client.post(url, headers=headers(pair), json=request)
    assert first.status_code == 200, first.text
    target = ConflictComparison if kind == "conflict" else RuleEvaluation
    original = AsyncSession.scalar
    missed = False

    async def miss_once(db, statement, *args, **kwargs):
        nonlocal missed
        descriptions = getattr(statement, "column_descriptions", [])
        if not missed and descriptions and descriptions[0].get("entity") == target:
            missed = True
            return None
        return await original(db, statement, *args, **kwargs)

    with patch.object(AsyncSession, "scalar", new=miss_once):
        response = client.post(url, headers=headers(pair), json=request)
    assert missed and response.status_code == 200, response.text
    assert response.json()["replayed"] and response.json()["id"] == first.json()["id"]
