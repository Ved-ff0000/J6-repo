from uuid import UUID, uuid4

import pytest
from test_auth import create_member, headers, login, register
from test_extraction import lease_model, lease_workflow, start  # noqa:F401


@pytest.fixture
def review_case(request):
    case = request.getfixturevalue("lease_workflow")
    client, pair, _doc, _prefix = case
    with lease_model():
        run = start(case).json()
    response = client.post(f"/api/v1/workflow-runs/{run['id']}/reviews", headers=headers(pair))
    assert response.status_code == 200, response.text
    return client, pair, run, response.json()


@pytest.mark.parametrize("action", ["ACCEPT", "REJECT", "MODIFY"])
def test_human_decision_preserves_original_requires_reason_and_is_idempotent(review_case, action):
    client, pair, run, tasks = review_case
    task = tasks[0]
    body = {
        "request_key": str(uuid4()),
        "expected_revision": 1,
        "action": action,
        "reason": "Reviewed exact original source text.",
    }
    if action == "MODIFY":
        body.update(
            value=task["original"]["value"],
            quote=task["original"]["evidence_text"],
            evidence_chunk_id=task["original"]["citation"]["chunk_id"],
        )
    url = f"/api/v1/reviews/{task['id']}/decision"
    first = client.post(url, headers=headers(pair), json=body)
    assert first.status_code == 200, first.text
    assert first.json()["original"] == task["original"]
    assert (first.json()["reviewed"] is None) == (action == "REJECT")
    assert client.post(url, headers=headers(pair), json=body).json()["id"] == first.json()["id"]
    assert (
        client.post(
            url, headers=headers(pair), json={**body, "request_key": str(uuid4())}
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/workflow-runs/{run['id']}", headers=headers(pair)).json()["result"]
        == run["result"]
    )
    assert client.get("/api/v1/audit/verify", headers=headers(pair)).json()["valid"]
    assert (
        client.get("/api/v1/reviews", headers=headers(pair), params={"status": "DECIDED"}).json()[
            0
        ]["id"]
        == task["id"]
    )


def test_review_security_and_evidence(review_case):
    client, pair, _run, tasks = review_case
    task = tasks[0]
    url = f"/api/v1/reviews/{task['id']}/decision"
    body = {
        "request_key": str(uuid4()),
        "expected_revision": 1,
        "action": "MODIFY",
        "reason": "Reviewed original text carefully.",
        "value": "Invented landlord",
        "quote": task["original"]["evidence_text"],
        "evidence_chunk_id": task["original"]["citation"]["chunk_id"],
    }
    assert client.post(url, headers=headers(pair), json=body).status_code == 422
    assert (
        client.post(url, headers=headers(pair), json={**body, "reason": "        "}).status_code
        == 422
    )
    beta = register(client, "beta", email="beta@example.com")
    assert client.get(f"/api/v1/reviews/{task['id']}", headers=headers(beta)).status_code == 404
    create_member(client, pair, "VIEWER")
    viewer = login(client, "member@example.com")
    assert client.post(url, headers=headers(viewer), json=body).status_code == 403
    assert client.get("/api/v1/audit", headers=headers(viewer)).status_code == 403
    assert client.get("/api/v1/reviews", headers=headers(viewer)).status_code == 403


def test_audit_detects_tampering_and_scopes_org(review_case):
    from sqlalchemy import update

    from app.governance.models import AuditEvent

    client, pair, _run, _tasks = review_case
    events = client.get("/api/v1/audit", headers=headers(pair)).json()
    assert len(events) > 2

    async def tamper():
        async with client.app.state.test_factory() as db, db.begin():
            await db.execute(
                update(AuditEvent)
                .where(AuditEvent.id == UUID(events[0]["id"]))
                .values(event_type="TAMPERED")
            )

    client.portal.call(tamper)
    assert client.get("/api/v1/audit/verify", headers=headers(pair)).json()["valid"] is False
    beta = register(client, "beta", email="beta@example.com")
    assert all(
        event["id"] not in {e["id"] for e in events}
        for event in client.get("/api/v1/audit", headers=headers(beta)).json()
    )


@pytest.mark.parametrize("change", ["checksum", "new_version"])
def test_review_refuses_stale_inputs(review_case, change):
    from app.documents.models import Document, DocumentVersion

    client, pair, run, tasks = review_case

    async def changed():
        async with client.app.state.test_factory() as db, db.begin():
            if change == "new_version":
                document = await db.get(Document, UUID(run["document_id"]))
                document.current_version = 2
            else:
                version = await db.get(DocumentVersion, UUID(run["version_id"]))
                version.checksum = "f" * 64

    client.portal.call(changed)
    response = client.post(
        f"/api/v1/reviews/{tasks[0]['id']}/decision",
        headers=headers(pair),
        json={
            "request_key": str(uuid4()),
            "expected_revision": 1,
            "action": "ACCEPT",
            "reason": "Checked original exact evidence.",
        },
    )
    assert response.status_code == 409, response.text


def test_reviewer_can_decide_but_auditor_cannot(review_case):
    client, pair, _run, tasks = review_case
    create_member(client, pair, "REVIEWER")
    reviewer = login(client, "member@example.com")
    response = client.post(
        f"/api/v1/reviews/{tasks[0]['id']}/decision",
        headers=headers(reviewer),
        json={
            "request_key": str(uuid4()),
            "expected_revision": 1,
            "action": "REJECT",
            "reason": "The quoted source does not establish this role.",
        },
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/audit", headers=headers(reviewer)).status_code == 403
    assert client.get("/api/v1/extractions", headers=headers(reviewer)).status_code == 200


def test_auto_enqueue_is_explicit_and_keeps_source_unverified(request):
    case = request.getfixturevalue("lease_workflow")
    client, pair, _doc, _prefix = case
    client.app.state.settings.review_auto_enqueue = True
    with lease_model():
        response = start(case)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["review_queue_created"] is True
    tasks = client.get("/api/v1/reviews", headers=headers(pair)).json()
    assert len(tasks) == 12 and all(
        t["original"]["status"] == "MODEL_PROPOSED_UNVERIFIED" for t in tasks
    )
