import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from test_auth import headers, register
from test_extraction import lease_workflow  # noqa:F401
from test_governance import review_case  # noqa:F401

from app.operations.worker import run_once


@pytest.fixture
def ops_case(request):
    return request.getfixturevalue("review_case")


def test_notifications_preferences_reads_and_actual_analytics(ops_case):
    client, pair, _run, _tasks = ops_case
    h = headers(pair)
    notifications = client.get("/api/v1/notifications", headers=h)
    assert notifications.status_code == 200 and notifications.json(), notifications.text
    event = notifications.json()[0]
    assert client.post(f"/api/v1/notifications/{event['id']}/read", headers=h).status_code == 204
    assert client.get("/api/v1/notifications", headers=h).json()[0]["read"]
    assert (
        client.put("/api/v1/notifications/preferences", headers=h, json={"email": True}).status_code
        == 503
    )
    assert (
        client.put(
            "/api/v1/notifications/preferences", headers=h, json={"in_app": False}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/notifications", headers=h).json() == []
    usage = client.get("/api/v1/billing/usage", headers=h).json()
    assert (
        usage["uploaded_versions"] == 1
        and usage["retained_bytes"] > 0
        and usage["actual_cost"] is None
    )
    summary = client.get("/api/v1/analytics/summary", headers=h)
    assert summary.status_code == 200, summary.text
    assert summary.json()["reviews"]["OPEN"] == 12 and summary.json()["ai_accuracy"] is None
    assert client.get("/api/v1/analytics/deadlines", headers=h).json()["items"] == []
    beta = register(client, "beta", email="beta@example.com")
    assert (
        client.post(f"/api/v1/notifications/{event['id']}/read", headers=headers(beta)).status_code
        == 404
    )
    assert (
        client.get("/api/v1/billing/usage", headers=headers(beta)).json()["uploaded_versions"] == 0
    )


@pytest.mark.parametrize("target", ["salesforce_mock", "sap_mock", "servicenow_mock"])
def test_integrations_explicit_simulations_only_and_idempotent(ops_case, target):
    client, pair, _run, tasks = ops_case
    h = headers(pair)
    decision = client.post(
        f"/api/v1/reviews/{tasks[0]['id']}/decision",
        headers=h,
        json={
            "request_key": str(uuid4()),
            "expected_revision": 1,
            "action": "ACCEPT",
            "reason": "Verified the exact source quotation.",
        },
    )
    assert decision.status_code == 200, decision.text
    body = {"request_key": str(uuid4()), "decision_id": decision.json()["id"], "target": target}
    with patch("httpx.AsyncClient") as network:
        first = client.post("/api/v1/integrations", headers=h, json=body)
    network.assert_not_called()
    assert first.status_code == 200, first.text
    assert (
        first.json()["status"] == "SIMULATED"
        and first.json()["result"]["external_request_made"] is False
    )
    assert (
        client.post("/api/v1/integrations", headers=h, json=body).json()["id"] == first.json()["id"]
    )
    assert (
        client.post(
            "/api/v1/integrations", headers=h, json={**body, "decision_id": str(uuid4())}
        ).status_code
        == 404
    )


def test_delivery_failure_retries_and_success_acknowledges_without_private_text(ops_case):
    from sqlalchemy import select, update

    from app.auth.models import Organization
    from app.auth.security import utcnow
    from app.governance.service import audit
    from app.operations.models import Delivery

    client, pair, _run, _tasks = ops_case
    h = headers(pair)
    settings = client.app.state.settings
    settings.email_enabled = True
    assert (
        client.put("/api/v1/notifications/preferences", headers=h, json={"email": True}).status_code
        == 200
    )

    async def event():
        async with client.app.state.test_factory() as db, db.begin():
            org = await db.scalar(select(Organization.id))
            await audit(db, org, "REVIEW_CREATED", "workflow", uuid4())

    client.portal.call(event)
    factory = client.app.state.test_factory
    with patch(
        "app.operations.worker.SMTPTransport.send",
        new=AsyncMock(side_effect=OSError("private secret")),
    ):
        assert client.portal.call(run_once, factory, settings)
    deliveries = client.get("/api/v1/notifications/deliveries", headers=h).json()
    assert (
        deliveries[0]["status"] == "PENDING"
        and deliveries[0]["error_code"] == "TRANSPORT_UNAVAILABLE"
    )

    async def due():
        async with factory() as db, db.begin():
            await db.execute(update(Delivery).values(next_attempt_at=utcnow()))

    client.portal.call(due)
    with patch("app.operations.worker.SMTPTransport.send", new=AsyncMock()) as send:
        assert client.portal.call(run_once, factory, settings)
    assert "Landlord" not in json.dumps(send.call_args.args)
    assert client.get("/api/v1/notifications/deliveries", headers=h).json()[0]["status"] == "SENT"
    assert not client.portal.call(run_once, factory, settings)


def test_live_tickets_are_single_use_and_events_never_contain_private_audit_payloads(ops_case):
    client, pair, _run, _tasks = ops_case
    assert client.post("/api/v1/events/ticket", headers=headers(pair)).status_code == 503
    client.app.state.settings.live_enabled = True
    ticket = client.post("/api/v1/events/ticket", headers=headers(pair)).json()["ticket"]
    with client.websocket_connect("/api/v1/events/live") as socket:
        socket.send_json({"ticket": ticket, "after": 0})
        message = socket.receive_json()
        assert message["type"] == "invalidate" and set(message) == {"type", "sequence"}
    from starlette.websockets import WebSocketDisconnect

    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/v1/events/live") as socket,
    ):
        socket.send_json({"ticket": ticket, "after": 0})
        socket.receive_json()


def test_metrics_have_template_labels_not_tenant_ids_or_document_values(ops_case):
    client, pair, _run, _tasks = ops_case
    h = headers(pair)
    client.app.state.settings.metrics_enabled = True
    assert client.get("/api/v1/observability/metrics", headers=h).status_code == 403
    from sqlalchemy import update

    from app.auth.models import User

    async def promote():
        async with client.app.state.test_factory() as db, db.begin():
            await db.execute(update(User).values(role="SUPER_ADMIN"))

    client.portal.call(promote)
    response = client.get("/api/v1/observability/metrics", headers=h)
    assert response.status_code == 200 and "leaseguard_http_requests_total" in response.text
    assert "Acme Estates" not in response.text and pair["access_token"] not in response.text
    assert client.get("/api/v1/observability/metrics").status_code == 401


def test_private_api_headers_and_error_bodies_do_not_echo_secrets(ops_case):
    client, pair, _run, _tasks = ops_case
    response = client.post(
        "/api/v1/integrations",
        headers=headers(pair),
        json={
            "target": "secret-value-do-not-echo",
            "decision_id": "invalid",
            "request_key": "invalid",
        },
    )
    assert response.status_code == 422 and "secret-value-do-not-echo" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
