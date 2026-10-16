from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import Principal, Session, require_permission
from app.auth.models import AuthSession, Organization, User
from app.auth.security import utcnow
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentStatus, DocumentVersion
from app.rules.catalog import policy
from app.rules.engine import evaluate, input_hash
from app.rules.models import RuleEvaluation
from app.rules.schemas import RuleReport
from app.search.router import citation
from app.workflows.models import WorkflowRun
from app.workflows.schemas import WorkflowResult
from app.workflows.service import visible_version

router = APIRouter(
    prefix="/api/v1",
    tags=["deterministic rules"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)},
)
Admins = Annotated[Principal, Depends(require_permission("ai:manage"))]


class EvaluationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workflow_run_id: UUID
    document_id: UUID
    version_id: UUID
    request_key: UUID
    input_sha256: str
    policy_sha256: str
    report: RuleReport
    created_at: datetime
    replayed: bool = False


async def parent(db: Session, org: UUID, run_id: UUID, *, lock: bool = False) -> WorkflowRun:
    query = select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.organization_id == org)
    run = await db.scalar(query.with_for_update() if lock else query)
    if run is None:
        raise HTTPException(404, "Workflow run not found")
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == run.version_id,
            DocumentVersion.document_id == run.document_id,
            DocumentVersion.organization_id == org,
        )
    )
    if version is None:
        raise HTTPException(404, "Workflow source not found")
    await visible_version(db, org, run.document_id, version.version_number)
    return run


@router.get("/rules/catalog")
async def catalog(request: Request, principal: Admins) -> dict[str, object]:
    settings = request.app.state.settings
    current = policy(settings.rules_enabled, settings.rules_disabled)
    return {
        "policy": current.model_dump(mode="json"),
        "immutable_definitions": True,
        "automatic_approval": False,
        "external_calls": False,
    }


@router.post("/workflow-runs/{run_id}/rule-evaluations", response_model=EvaluationOut)
async def create(
    run_id: UUID, body: EvaluationIn, request: Request, principal: Admins, db: Session
) -> EvaluationOut:
    org = principal.organization_id
    run = await parent(db, org, run_id, lock=True)
    existing = await db.scalar(
        select(RuleEvaluation).where(
            RuleEvaluation.organization_id == org,
            RuleEvaluation.request_key == body.request_key,
        )
    )
    if existing:
        if existing.workflow_run_id != run.id:
            raise HTTPException(409, "RULE_REQUEST_KEY_CONFLICT")
        return EvaluationOut.model_validate(existing).model_copy(update={"replayed": True})
    settings = request.app.state.settings
    if not settings.rules_enabled:
        raise HTTPException(503, "RULES_DISABLED")
    if (
        run.status != "REVIEW_REQUIRED"
        or run.extraction_mode != "LEASE_FIELDS"
        or run.result is None
    ):
        raise HTTPException(409, "RULE_INPUT_UNAVAILABLE")
    result = WorkflowResult.model_validate(run.result)
    lease = result.lease_extraction
    if lease is None:
        raise HTTPException(409, "RULE_INPUT_UNAVAILABLE")
    document = await db.scalar(
        select(Document)
        .where(Document.id == run.document_id, Document.organization_id == org)
        .with_for_update()
    )
    version = await db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == run.version_id,
            DocumentVersion.organization_id == org,
            DocumentVersion.document_id == run.document_id,
        )
        .with_for_update()
    )
    if (
        document is None
        or version is None
        or document.status == DocumentStatus.ARCHIVED
        or document.current_version != version.version_number
        or version.processing_status != "PARSED"
        or version.checksum != run.source_checksum
    ):
        raise HTTPException(409, "RULE_SOURCE_CHANGED")
    for field in lease.fields:
        source = field.citation
        if source.document_id != run.document_id or source.version_id != run.version_id:
            raise HTTPException(409, "RULE_SOURCE_CHANGED")
        fresh = await citation(db, org, source.chunk_id)
        if fresh.model_dump() != source.model_dump():
            raise HTTPException(409, "RULE_SOURCE_CHANGED")
    report = evaluate(lease, policy(True, settings.rules_disabled), utcnow())
    # Fresh scalar authorization, not a cached ORM role, before final publication.
    active = await db.scalar(
        select(User.id)
        .join(Organization, Organization.id == User.organization_id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            User.id == principal.user.id,
            User.organization_id == org,
            User.status == "ACTIVE",
            User.role.in_(["ORG_ADMIN", "SUPER_ADMIN"]),
            Organization.status == "ACTIVE",
            AuthSession.id == principal.session.id,
            AuthSession.organization_id == org,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .with_for_update()
    )
    if active is None:
        raise HTTPException(403, "RULE_AUTHORIZATION_REVOKED")
    record = RuleEvaluation(
        organization_id=org,
        document_id=run.document_id,
        version_id=run.version_id,
        workflow_run_id=run.id,
        requested_by=principal.user.id,
        auth_session_id=principal.session.id,
        request_key=body.request_key,
        input_sha256=input_hash(lease),
        policy_sha256=report.policy_sha256,
        report=report.model_dump(mode="json"),
    )
    try:
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(RuleEvaluation).where(
                RuleEvaluation.organization_id == org,
                RuleEvaluation.request_key == body.request_key,
            )
        )
        if existing is not None:
            if existing.workflow_run_id != run_id:
                raise HTTPException(409, "RULE_REQUEST_KEY_CONFLICT") from None
            return EvaluationOut.model_validate(existing).model_copy(update={"replayed": True})
        raise HTTPException(409, "RULE_SOURCE_OR_AUTH_CHANGED") from None

    from app.governance.service import audit

    await audit(
        db,
        org,
        "RULE_EXECUTED",
        "rule_evaluation",
        record.id,
        principal.user.id,
        request.state.request_id,
    )
    response = EvaluationOut.model_validate(record)
    await db.commit()
    return response


@router.get("/workflow-runs/{run_id}/rule-evaluations", response_model=list[EvaluationOut])
async def history(
    run_id: UUID,
    principal: Admins,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RuleEvaluation]:
    await parent(db, principal.organization_id, run_id)
    return list(
        await db.scalars(
            select(RuleEvaluation)
            .where(
                RuleEvaluation.organization_id == principal.organization_id,
                RuleEvaluation.workflow_run_id == run_id,
            )
            .order_by(RuleEvaluation.created_at.desc(), RuleEvaluation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/rule-evaluations/{evaluation_id}", response_model=EvaluationOut)
async def get(evaluation_id: UUID, principal: Admins, db: Session) -> RuleEvaluation:
    record = await db.scalar(
        select(RuleEvaluation).where(
            RuleEvaluation.id == evaluation_id,
            RuleEvaluation.organization_id == principal.organization_id,
        )
    )
    if record is None:
        raise HTTPException(404, "Rule evaluation not found")
    await parent(db, principal.organization_id, record.workflow_run_id)
    return record
