from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.auth.dependencies import Principal, Session, require_permission
from app.auth.models import AuthSession, Organization, User
from app.auth.security import utcnow
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentVersion
from app.extraction.schemas import LeaseExtraction, LeaseField
from app.extraction.validation import validate_lease_fields
from app.governance.models import AuditEvent, ReviewDecision, ReviewTask
from app.governance.service import ZERO, audit, enqueue_reviews, event_data
from app.rules.engine import exact, fingerprint, input_hash
from app.rules.router import parent
from app.search.router import citation
from app.workflows.errors import WorkflowFailure
from app.workflows.schemas import WorkflowResult

router = APIRouter(
    prefix="/api/v1",
    tags=["human review and audit"],
    responses={c: {"model": ErrorEnvelope} for c in (401, 403, 404, 409, 503)},
)
Reviewers = Annotated[Principal, Depends(require_permission("reviews:write"))]
Readers = Annotated[Principal, Depends(require_permission("reviews:read"))]
Auditors = Annotated[Principal, Depends(require_permission("audit:read"))]
Admins = Annotated[Principal, Depends(require_permission("ai:manage"))]


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    run_id: UUID
    document_id: UUID
    version_id: UUID
    field_index: int
    status: str
    revision: int
    original: LeaseField
    created_at: datetime


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["ACCEPT", "MODIFY", "REJECT"]
    reason: str = Field(min_length=8, max_length=2000)
    value: str | None = Field(default=None, min_length=1, max_length=300)
    evidence_chunk_id: UUID | None = None
    quote: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if len(value.strip()) < 8:
            raise ValueError("Meaningful review reason required")
        return value.strip()


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    task_id: UUID
    reviewer_id: UUID
    action: str
    reason: str
    original: LeaseField
    reviewed: LeaseField | None
    created_at: datetime


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sequence: int
    actor_id: UUID | None
    event_type: str
    resource_type: str
    resource_id: UUID
    request_id: str
    payload: dict[str, object]
    previous_sha256: str
    sha256: str
    created_at: datetime


async def task_source(db: Session, org: UUID, task: ReviewTask) -> None:
    await parent(db, org, task.run_id)


@router.get("/reviews", response_model=list[TaskOut])
async def queue(
    principal: Readers,
    db: Session,
    status: Literal["OPEN", "DECIDED"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ReviewTask]:
    rows = await db.scalars(
        select(ReviewTask)
        .join(Document, Document.id == ReviewTask.document_id)
        .where(
            ReviewTask.organization_id == principal.organization_id,
            Document.organization_id == principal.organization_id,
            Document.status != "ARCHIVED",
            *([ReviewTask.status == status] if status else []),
        )
        .order_by(ReviewTask.created_at, ReviewTask.id)
        .offset(offset)
        .limit(limit)
    )
    return list(rows)


@router.post("/workflow-runs/{run_id}/reviews", response_model=list[TaskOut])
async def backfill(run_id: UUID, principal: Admins, db: Session) -> list[TaskOut]:
    run = await parent(db, principal.organization_id, run_id, lock=True)
    if run.status != "REVIEW_REQUIRED" or run.extraction_mode != "LEASE_FIELDS":
        raise HTTPException(409, "REVIEW_INPUT_UNAVAILABLE")
    await enqueue_reviews(db, run)
    rows = list(
        await db.scalars(
            select(ReviewTask)
            .where(
                ReviewTask.organization_id == principal.organization_id, ReviewTask.run_id == run.id
            )
            .order_by(ReviewTask.field_index)
        )
    )
    response = [TaskOut.model_validate(row) for row in rows]
    await db.commit()
    return response


@router.get("/reviews/{task_id}", response_model=TaskOut)
async def get_task(task_id: UUID, principal: Readers, db: Session) -> ReviewTask:
    task = await db.scalar(
        select(ReviewTask).where(
            ReviewTask.id == task_id, ReviewTask.organization_id == principal.organization_id
        )
    )
    if task is None:
        raise HTTPException(404, "Review not found")
    await task_source(db, principal.organization_id, task)
    return task


@router.get("/reviews/{task_id}/decision", response_model=DecisionOut | None)
async def get_decision(task_id: UUID, principal: Readers, db: Session) -> ReviewDecision | None:
    await get_task(task_id, principal, db)
    return await db.scalar(
        select(ReviewDecision).where(
            ReviewDecision.task_id == task_id,
            ReviewDecision.organization_id == principal.organization_id,
        )
    )


@router.post("/reviews/{task_id}/decision", response_model=DecisionOut)
async def decide(
    task_id: UUID, body: DecisionIn, request: Request, principal: Reviewers, db: Session
) -> DecisionOut:
    org = principal.organization_id
    task = await db.scalar(
        select(ReviewTask)
        .where(ReviewTask.id == task_id, ReviewTask.organization_id == org)
        .with_for_update()
    )
    if task is None:
        raise HTTPException(404, "Review not found")
    await task_source(db, org, task)
    signature = fingerprint(body.model_dump(mode="json", exclude={"request_key"}))
    previous = await db.scalar(
        select(ReviewDecision).where(
            ReviewDecision.organization_id == org, ReviewDecision.request_key == body.request_key
        )
    )
    if previous:
        if previous.task_id != task.id or previous.request_sha256 != signature:
            raise HTTPException(409, "REVIEW_REQUEST_KEY_CONFLICT")
        return DecisionOut.model_validate(previous)
    if task.status != "OPEN" or task.revision != body.expected_revision:
        raise HTTPException(409, "REVIEW_ALREADY_DECIDED_OR_CHANGED")
    run = await parent(db, org, task.run_id, lock=True)
    document = await db.scalar(
        select(Document)
        .where(Document.id == task.document_id, Document.organization_id == org)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    version = await db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.id == task.version_id, DocumentVersion.organization_id == org)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        document is None
        or version is None
        or document.current_version != version.version_number
        or version.processing_status != "PARSED"
        or version.checksum != run.source_checksum
    ):
        raise HTTPException(409, "REVIEW_SOURCE_STALE")
    result = WorkflowResult.model_validate(run.result)
    lease = result.lease_extraction
    if (
        lease is None
        or input_hash(lease) != task.source_sha256
        or task.field_index >= len(lease.fields)
        or lease.fields[task.field_index].model_dump(mode="json") != task.original
    ):
        raise HTTPException(409, "REVIEW_SOURCE_CHANGED")
    original = LeaseField.model_validate(task.original)
    fresh = await citation(db, org, original.citation.chunk_id)
    if fresh.model_dump() != original.citation.model_dump() or not exact(original):
        raise HTTPException(409, "REVIEW_EVIDENCE_CHANGED")
    reviewed = original if body.action == "ACCEPT" else None
    if body.action == "MODIFY":
        if not body.value or not body.quote or not body.evidence_chunk_id:
            raise HTTPException(422, "MODIFY_REQUIRES_EXACT_SOURCE_VALUE_AND_QUOTE")
        evidence = await citation(db, org, body.evidence_chunk_id)
        if evidence.document_id != task.document_id or evidence.version_id != task.version_id:
            raise HTTPException(409, "REVIEW_EVIDENCE_WRONG_SOURCE")
        try:
            proposal = LeaseExtraction.model_validate(
                {
                    original.group: [
                        {
                            "field": original.field,
                            "value": body.value,
                            "quote": body.quote,
                            "chunk_id": str(evidence.chunk_id),
                            "interpretation": "EXPLICIT",
                        }
                    ]
                }
            )
            reviewed = validate_lease_fields(proposal, [evidence]).fields[0]
        except (ValueError, IndexError, WorkflowFailure) as exc:
            raise HTTPException(422, "MODIFIED_VALUE_MUST_LINK_TO_EXACT_SOURCE") from exc
    elif any(v is not None for v in (body.value, body.quote, body.evidence_chunk_id)):
        raise HTTPException(422, "UNEXPECTED_MODIFICATION_FIELDS")
    active = await db.scalar(
        select(User.id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .join(Organization, Organization.id == User.organization_id)
        .where(
            User.id == principal.user.id,
            User.organization_id == org,
            User.status == "ACTIVE",
            User.role.in_(["ORG_ADMIN", "SUPER_ADMIN", "REVIEWER"]),
            Organization.status == "ACTIVE",
            AuthSession.id == principal.session.id,
            AuthSession.organization_id == org,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if active is None:
        raise HTTPException(403, "REVIEW_AUTHORIZATION_REVOKED")
    decision = ReviewDecision(
        organization_id=org,
        task_id=task.id,
        request_key=body.request_key,
        request_sha256=signature,
        reviewer_id=principal.user.id,
        action=body.action,
        reason=body.reason,
        original=task.original,
        reviewed=reviewed.model_dump(mode="json") if reviewed else None,
    )
    db.add(decision)
    task.status = "DECIDED"
    task.revision += 1
    await db.flush()
    await audit(
        db,
        org,
        "REVIEW_" + body.action,
        "review",
        task.id,
        principal.user.id,
        request.state.request_id,
        {"decision_id": str(decision.id), "reason": body.reason, "lease_approved": False},
    )
    response = DecisionOut.model_validate(decision)
    await db.commit()
    return response


@router.get("/audit", response_model=list[AuditOut])
async def events(
    principal: Auditors,
    db: Session,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEvent]:
    return list(
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.organization_id == principal.organization_id, AuditEvent.sequence > after
            )
            .order_by(AuditEvent.sequence)
            .limit(limit)
        )
    )


@router.get("/audit/verify")
async def verify(
    principal: Auditors,
    db: Session,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> dict[str, object]:
    prev = (
        await db.scalar(
            select(AuditEvent).where(
                AuditEvent.organization_id == principal.organization_id,
                AuditEvent.sequence == after,
            )
        )
        if after
        else None
    )
    if after and prev is None:
        raise HTTPException(409, "AUDIT_ANCHOR_NOT_FOUND")
    rows = await events(principal, db, after, limit)
    digest = prev.sha256 if prev else ZERO
    seq = after
    valid = True
    for row in rows:
        valid = (
            valid
            and row.previous_sha256 == digest
            and row.sequence == seq + 1
            and row.sha256 == fingerprint(event_data(row))
        )
        digest = row.sha256
        seq = row.sequence
    total = await db.scalar(
        select(func.max(AuditEvent.sequence)).where(
            AuditEvent.organization_id == principal.organization_id
        )
    )
    return {
        "valid": valid,
        "through_sequence": seq,
        "anchor_sha256": digest,
        "more": bool(total and total > seq),
        "limitation": "Retain external anchors; this cannot prove absence of tail truncation or privileged chain reconstruction.",
    }


@router.get("/review-decisions", response_model=list[DecisionOut])
async def decision_history(
    principal: Readers, db: Session, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[ReviewDecision]:
    return list(
        await db.scalars(
            select(ReviewDecision)
            .join(ReviewTask, ReviewTask.id == ReviewDecision.task_id)
            .join(Document, Document.id == ReviewTask.document_id)
            .where(
                ReviewDecision.organization_id == principal.organization_id,
                ReviewTask.organization_id == principal.organization_id,
                Document.organization_id == principal.organization_id,
                Document.status != "ARCHIVED",
            )
            .order_by(ReviewDecision.created_at.desc())
            .limit(limit)
        )
    )


@router.get("/extractions")
async def extraction_list(
    principal: Readers, db: Session, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> list[dict[str, object]]:
    from app.workflows.models import WorkflowRun

    rows = await db.scalars(
        select(WorkflowRun)
        .join(Document, Document.id == WorkflowRun.document_id)
        .join(DocumentVersion, DocumentVersion.id == WorkflowRun.version_id)
        .where(
            WorkflowRun.organization_id == principal.organization_id,
            Document.organization_id == principal.organization_id,
            DocumentVersion.organization_id == principal.organization_id,
            Document.status != "ARCHIVED",
            DocumentVersion.version_number == Document.current_version,
            WorkflowRun.status == "REVIEW_REQUIRED",
            WorkflowRun.extraction_mode == "LEASE_FIELDS",
        )
        .order_by(WorkflowRun.created_at.desc())
        .limit(limit)
    )
    results: list[dict[str, object]] = []
    for row in rows:
        result = WorkflowResult.model_validate(row.result)
        if result.lease_extraction is not None:
            results.append(
                {
                    "id": str(row.id),
                    "document_id": str(row.document_id),
                    "version_id": str(row.version_id),
                    "lease_extraction": result.lease_extraction.model_dump(mode="json"),
                    "rule_report": result.rule_report.model_dump(mode="json")
                    if result.rule_report
                    else None,
                }
            )
    return results
