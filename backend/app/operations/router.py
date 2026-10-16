import hashlib
import secrets
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import SQLColumnExpression, func, select

from app.ai.models import ProviderRun
from app.auth.dependencies import CurrentPrincipal, Principal, Session, require_permission
from app.auth.models import Organization
from app.auth.security import utcnow
from app.conflicts.models import ConflictComparison
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentVersion
from app.governance.models import AuditEvent, ReviewDecision, ReviewTask
from app.governance.router import task_source
from app.governance.service import audit
from app.operations.integrations import PROVIDERS
from app.operations.models import (
    Delivery,
    IntegrationRun,
    LiveTicket,
    NotificationPreference,
    NotificationReceipt,
)
from app.processing.models import ProcessingJob
from app.rules.engine import fingerprint
from app.workflows.models import WorkflowRun

router = APIRouter(
    prefix="/api/v1",
    tags=["workspace operations"],
    responses={c: {"model": ErrorEnvelope} for c in (401, 403, 404, 409, 503)},
)
Admins = Annotated[Principal, Depends(require_permission("ai:manage"))]
Analysts = Annotated[Principal, Depends(require_permission("documents:read"))]
NOTIFY_TYPES = (
    "REVIEW_CREATED",
    "PROCESSING_FAILED",
    "CONFLICT_COMPARED",
    "REVIEW_ACCEPT",
    "REVIEW_MODIFY",
    "REVIEW_REJECT",
    "INTEGRATION_SIMULATED",
    "DEADLINE_APPROACHING",
)


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    in_app: bool = True
    email: bool = False
    webhook: bool = False


class PreferenceOut(PreferenceIn):
    pass


class NotificationOut(BaseModel):
    id: UUID
    event_type: str
    resource_id: UUID
    sequence: int
    read: bool


class IntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    decision_id: UUID
    target: Literal["salesforce_mock", "sap_mock", "servicenow_mock"]


class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    decision_id: UUID
    target: str
    status: str
    result: dict[str, object]


@router.get("/notifications/preferences", response_model=PreferenceOut)
async def preferences(principal: CurrentPrincipal, db: Session) -> PreferenceOut:
    row = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.organization_id == principal.organization_id,
            NotificationPreference.user_id == principal.user.id,
        )
    )
    return (
        PreferenceOut(in_app=row.in_app, email=row.email, webhook=row.webhook)
        if row
        else PreferenceOut()
    )


@router.put("/notifications/preferences", response_model=PreferenceOut)
async def set_preferences(
    body: PreferenceIn, request: Request, principal: CurrentPrincipal, db: Session
) -> PreferenceOut:
    cfg = request.app.state.settings
    if body.email and not cfg.email_enabled:
        raise HTTPException(503, "EMAIL_TRANSPORT_DISABLED")
    if body.webhook and not cfg.webhook_enabled:
        raise HTTPException(503, "WEBHOOK_TRANSPORT_DISABLED")
    await db.scalar(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    row = await db.scalar(
        select(NotificationPreference)
        .where(
            NotificationPreference.organization_id == principal.organization_id,
            NotificationPreference.user_id == principal.user.id,
        )
        .with_for_update()
    )
    if row is None:
        sequence = await db.scalar(
            select(func.max(AuditEvent.sequence)).where(
                AuditEvent.organization_id == principal.organization_id
            )
        )
        row = NotificationPreference(
            organization_id=principal.organization_id,
            user_id=principal.user.id,
            after_sequence=sequence or 0,
        )
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    await audit(
        db,
        principal.organization_id,
        "NOTIFICATION_PREFERENCES_CHANGED",
        "user",
        principal.user.id,
        principal.user.id,
        request.state.request_id,
    )
    await db.commit()
    return PreferenceOut(**body.model_dump())


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(
    principal: CurrentPrincipal,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[NotificationOut]:
    pref = await preferences(principal, db)
    if not pref.in_app:
        return []
    rows = list(
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == principal.organization_id,
                AuditEvent.event_type.in_(NOTIFY_TYPES),
                AuditEvent.sequence > after,
            )
            .order_by(AuditEvent.sequence)
            .limit(limit)
        )
    )
    read = set(
        await db.scalars(
            select(NotificationReceipt.event_id).where(
                NotificationReceipt.organization_id == principal.organization_id,
                NotificationReceipt.user_id == principal.user.id,
                NotificationReceipt.event_id.in_([r.id for r in rows]),
            )
        )
    )
    return [
        NotificationOut(
            id=r.id,
            event_type=r.event_type,
            resource_id=r.resource_id,
            sequence=r.sequence,
            read=r.id in read,
        )
        for r in rows
    ]


@router.post("/notifications/{event_id}/read", status_code=204)
async def mark_read(event_id: UUID, principal: CurrentPrincipal, db: Session) -> None:
    await db.scalar(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    row = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.id == event_id,
            AuditEvent.organization_id == principal.organization_id,
            AuditEvent.event_type.in_(NOTIFY_TYPES),
        )
    )
    if row is None:
        raise HTTPException(404, "Notification not found")
    existing = await db.scalar(
        select(NotificationReceipt).where(
            NotificationReceipt.event_id == event_id,
            NotificationReceipt.user_id == principal.user.id,
            NotificationReceipt.organization_id == principal.organization_id,
        )
    )
    if existing is None:
        db.add(
            NotificationReceipt(
                organization_id=principal.organization_id,
                user_id=principal.user.id,
                event_id=event_id,
            )
        )
    await db.commit()


@router.get("/notifications/deliveries")
async def deliveries(
    principal: CurrentPrincipal, db: Session, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[dict[str, object]]:
    rows = await db.scalars(
        select(Delivery)
        .where(
            Delivery.organization_id == principal.organization_id,
            Delivery.user_id == principal.user.id,
        )
        .order_by(Delivery.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(r.id),
            "channel": r.channel,
            "status": r.status,
            "attempts": r.attempts,
            "error_code": r.error_code,
        }
        for r in rows
    ]


@router.get("/analytics/summary")
async def analytics(principal: Analysts, db: Session) -> dict[str, object]:
    org = principal.organization_id

    async def counts(
        model: type[Document]
        | type[ReviewTask]
        | type[WorkflowRun]
        | type[ProcessingJob]
        | type[ReviewDecision],
        column: SQLColumnExpression[str],
    ) -> dict[str, int]:
        # Closed call sites below supply mapped state columns, never caller-selected expressions.
        rows = await db.execute(
            select(column, func.count()).where(model.organization_id == org).group_by(column)
        )
        return {str(key): int(count) for key, count in rows}

    documents = await counts(Document, Document.status)
    reviews = await counts(ReviewTask, ReviewTask.status)
    workflows = await counts(WorkflowRun, WorkflowRun.status)
    processing = await counts(ProcessingJob, ProcessingJob.status)
    decisions = await counts(ReviewDecision, ReviewDecision.action)
    total = sum(decisions.values())
    conflicts = await db.scalar(
        select(func.count())
        .select_from(ConflictComparison)
        .where(ConflictComparison.organization_id == org)
    )
    return {
        "documents": documents,
        "processing": processing,
        "workflows": workflows,
        "reviews": reviews,
        "decisions": decisions,
        "comparison_records": conflicts or 0,
        "review_acceptance_rate": decisions.get("ACCEPT", 0) / total if total else None,
        "ai_accuracy": None,
        "calibrated_confidence": None,
        "quality_note": "Acceptance rate is human workflow activity, not AI accuracy or calibrated confidence. Comparisons are not a count of established legal conflicts.",
    }


@router.get("/analytics/deadlines")
async def deadlines(
    principal: Analysts, db: Session, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> dict[str, object]:
    # No deadlines are invented from unreviewed model proposals.
    rows = await db.execute(
        select(ReviewDecision, ReviewTask, Document, DocumentVersion)
        .join(ReviewTask, ReviewTask.id == ReviewDecision.task_id)
        .join(Document, Document.id == ReviewTask.document_id)
        .join(DocumentVersion, DocumentVersion.id == ReviewTask.version_id)
        .where(
            ReviewDecision.organization_id == principal.organization_id,
            ReviewTask.organization_id == principal.organization_id,
            Document.organization_id == principal.organization_id,
            DocumentVersion.organization_id == principal.organization_id,
            Document.status != "ARCHIVED",
            DocumentVersion.version_number == Document.current_version,
            ReviewDecision.action.in_(["ACCEPT", "MODIFY"]),
        )
        .order_by(ReviewDecision.created_at.desc())
        .limit(limit)
    )
    values = []
    for decision, task, _document, _version in rows:
        field = decision.reviewed or {}
        normalized = field.get("normalized")
        if (
            field.get("group") != "dates"
            or not isinstance(normalized, dict)
            or normalized.get("kind") != "date"
        ):
            continue
        iso = normalized.get("iso_date")
        if not isinstance(iso, str):
            continue
        values.append(
            {
                "task_id": str(task.id),
                "document_id": str(task.document_id),
                "field": field.get("field"),
                "date": iso,
                "days_from_today": (date.fromisoformat(iso) - utcnow().date()).days,
                "status": "HUMAN_REVIEWED_LITERAL_NOT_LEGAL_DEADLINE",
            }
        )
    return {
        "items": values,
        "scope": "Latest source versions, at most the requested number of recent human decisions; not all obligations or legal deadlines.",
    }


@router.get("/billing/usage")
async def usage(principal: Admins, db: Session) -> dict[str, object]:
    org = principal.organization_id
    versions, byte_count = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(DocumentVersion.file_size), 0)).where(
                DocumentVersion.organization_id == org
            )
        )
    ).one()
    diagnostics = (
        await db.execute(
            select(
                func.count(),
                func.sum(ProviderRun.total_tokens),
                func.count(ProviderRun.total_tokens),
            ).where(ProviderRun.organization_id == org)
        )
    ).one()
    runs = list(
        await db.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.organization_id == org)
            .order_by(WorkflowRun.created_at.desc())
            .limit(1000)
        )
    )
    calls = [c for run in runs for c in run.calls]
    tokens = 0
    unknown = 0
    for call in calls:
        reported = call.get("usage")
        total = reported.get("total_tokens") if isinstance(reported, dict) else None
        if isinstance(total, int):
            tokens += total
        else:
            unknown += 1
    return {
        "plan": principal.organization.plan,
        "uploaded_versions": versions,
        "retained_bytes": byte_count,
        "provider_diagnostic_requests": diagnostics[0],
        "reported_diagnostic_tokens": diagnostics[1],
        "diagnostics_with_usage": diagnostics[2],
        "workflow_scope": "latest 1000 workflow runs; not an invoice",
        "workflow_runs_in_scope": len(runs),
        "workflow_generations_in_scope": len(calls),
        "reported_workflow_tokens_in_scope": tokens,
        "workflow_calls_without_usage": unknown,
        "actual_cost": None,
        "currency": None,
        "billing_note": "Provider-reported tokens only; unknown usage/cost is not zero. No payment collection or price/invoice is implied.",
    }


@router.get("/integrations", response_model=list[IntegrationOut])
async def integrations(
    principal: Admins, db: Session, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[IntegrationRun]:
    return list(
        await db.scalars(
            select(IntegrationRun)
            .where(IntegrationRun.organization_id == principal.organization_id)
            .order_by(IntegrationRun.created_at.desc())
            .limit(limit)
        )
    )


@router.post("/integrations", response_model=IntegrationOut)
async def simulate(
    body: IntegrationIn, request: Request, principal: Admins, db: Session
) -> IntegrationOut:
    org = principal.organization_id
    await db.scalar(select(Organization.id).where(Organization.id == org).with_for_update())
    previous = await db.scalar(
        select(IntegrationRun).where(
            IntegrationRun.organization_id == org, IntegrationRun.request_key == body.request_key
        )
    )
    decision = await db.scalar(
        select(ReviewDecision).where(
            ReviewDecision.id == body.decision_id, ReviewDecision.organization_id == org
        )
    )
    if decision is None:
        raise HTTPException(404, "Human decision not found")
    task = await db.scalar(
        select(ReviewTask).where(
            ReviewTask.id == decision.task_id, ReviewTask.organization_id == org
        )
    )
    if task is None:
        raise HTTPException(404, "Review not found")
    await task_source(db, org, task)
    if previous:
        if (previous.target, previous.decision_id) != (body.target, body.decision_id):
            raise HTTPException(409, "INTEGRATION_REQUEST_KEY_CONFLICT")
        return IntegrationOut.model_validate(previous)
    if decision.action == "REJECT":
        raise HTTPException(409, "REJECTED_FIELD_NOT_ELIGIBLE")
    result = PROVIDERS[body.target].sync(
        {"decision_id": str(decision.id), "record_sha256": fingerprint(decision.reviewed)}
    )
    row = IntegrationRun(
        organization_id=org,
        decision_id=decision.id,
        requested_by=principal.user.id,
        request_key=body.request_key,
        target=body.target,
        result=result,
    )
    db.add(row)
    await db.flush()
    await audit(
        db,
        org,
        "INTEGRATION_SIMULATED",
        "integration",
        row.id,
        principal.user.id,
        request.state.request_id,
    )
    response = IntegrationOut.model_validate(row)
    await db.commit()
    return response


@router.post("/events/ticket")
async def ticket(request: Request, principal: CurrentPrincipal, db: Session) -> dict[str, object]:
    if not request.app.state.settings.live_enabled:
        raise HTTPException(503, "LIVE_UPDATES_DISABLED")
    await db.scalar(
        select(Organization.id)
        .where(Organization.id == principal.organization_id)
        .with_for_update()
    )
    # Bounded issuance; single-use 30-second tickets keep access tokens out of URLs/logs.
    count = await db.scalar(
        select(func.count())
        .select_from(LiveTicket)
        .where(
            LiveTicket.user_id == principal.user.id,
            LiveTicket.organization_id == principal.organization_id,
            LiveTicket.expires_at > utcnow(),
            LiveTicket.used_at.is_(None),
        )
    )
    if count and count >= 3:
        raise HTTPException(429, "TOO_MANY_LIVE_TICKETS")
    raw = secrets.token_urlsafe(32)
    db.add(
        LiveTicket(
            organization_id=principal.organization_id,
            user_id=principal.user.id,
            session_id=principal.session.id,
            token_sha256=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(seconds=30),
        )
    )
    await db.commit()
    return {"ticket": raw, "expires_in": 30}


@router.get("/observability/metrics")
async def metrics(request: Request, principal: Admins) -> Response:
    if principal.user.role != "SUPER_ADMIN":
        raise HTTPException(403, "PLATFORM_METRICS_REQUIRE_OPERATOR")
    if not request.app.state.settings.metrics_enabled:
        raise HTTPException(503, "METRICS_DISABLED")
    return Response(request.app.state.telemetry.render(), media_type="text/plain; version=0.0.4")
