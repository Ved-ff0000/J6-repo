from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import Session
from app.auth.models import AuthSession, Organization, User
from app.auth.security import utcnow
from app.conflicts.engine import compare
from app.conflicts.models import ConflictComparison
from app.conflicts.schemas import ComparisonReport, Source
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentMetadata, DocumentStatus, DocumentVersion
from app.rules.router import Admins, parent
from app.search.router import citation
from app.workflows.models import WorkflowRun
from app.workflows.schemas import WorkflowResult

router = APIRouter(
    prefix="/api/v1",
    tags=["conflict comparisons"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)},
)


class ComparisonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    left_run_id: UUID
    right_run_id: UUID


class ComparisonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    left_run_id: UUID
    right_run_id: UUID
    request_key: UUID
    report: ComparisonReport
    created_at: datetime
    replayed: bool = False


def replay(record: ConflictComparison, body: ComparisonIn) -> ComparisonOut:
    if (record.left_run_id, record.right_run_id) != (body.left_run_id, body.right_run_id):
        raise HTTPException(409, "COMPARISON_REQUEST_KEY_CONFLICT")
    return ComparisonOut.model_validate(record).model_copy(update={"replayed": True})


@router.get("/conflicts/config")
async def config(request: Request, principal: Admins) -> dict[str, object]:
    return {
        "enabled": request.app.state.settings.conflicts_enabled,
        "engine_revision": "proposal-comparison-v1",
        "external_calls": False,
        "authority_determined": False,
    }


async def source(db: Session, org: UUID, run: WorkflowRun) -> tuple[Source, WorkflowResult]:
    document = await db.scalar(
        select(Document)
        .where(Document.id == run.document_id, Document.organization_id == org)
        .execution_options(populate_existing=True)
    )
    version = await db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.id == run.version_id,
            DocumentVersion.document_id == run.document_id,
            DocumentVersion.organization_id == org,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if document is None or version is None or document.status == DocumentStatus.ARCHIVED:
        raise HTTPException(404, "Comparison source not found")
    if version.processing_status != "PARSED" or version.checksum != run.source_checksum:
        raise HTTPException(409, "COMPARISON_SOURCE_CHANGED")
    if (
        run.status != "REVIEW_REQUIRED"
        or run.extraction_mode != "LEASE_FIELDS"
        or run.result is None
    ):
        raise HTTPException(409, "COMPARISON_INPUT_UNAVAILABLE")
    result = WorkflowResult.model_validate(run.result)
    if result.lease_extraction is None:
        raise HTTPException(409, "COMPARISON_INPUT_UNAVAILABLE")
    for field in result.lease_extraction.fields:
        if (
            field.citation.document_id != run.document_id
            or field.citation.version_id != run.version_id
        ):
            raise HTTPException(409, "COMPARISON_SOURCE_CHANGED")
        fresh = await citation(db, org, field.citation.chunk_id)
        if fresh.model_dump() != field.citation.model_dump():
            raise HTTPException(409, "COMPARISON_SOURCE_CHANGED")
    metadata = await db.scalar(
        select(DocumentMetadata)
        .where(
            DocumentMetadata.document_id == run.document_id, DocumentMetadata.organization_id == org
        )
        .with_for_update()
    )
    return Source(
        run_id=run.id,
        document_id=run.document_id,
        version_id=run.version_id,
        version_number=version.version_number,
        checksum=version.checksum,
        version_created_at=version.created_at,
        parent_document_id=document.parent_document_id,
        document_type=metadata.document_type if metadata else "UNSPECIFIED",
    ), result


@router.post("/conflict-comparisons", response_model=ComparisonOut)
async def create(
    body: ComparisonIn, request: Request, principal: Admins, db: Session
) -> ComparisonOut:
    org = principal.organization_id
    if body.left_run_id == body.right_run_id:
        raise HTTPException(409, "DISTINCT_COMPARISON_SOURCES_REQUIRED")
    runs = {}
    for run_id in sorted((body.left_run_id, body.right_run_id), key=str):
        runs[run_id] = await parent(db, org, run_id, lock=True)
    existing = await db.scalar(
        select(ConflictComparison).where(
            ConflictComparison.organization_id == org,
            ConflictComparison.request_key == body.request_key,
        )
    )
    if existing is not None:
        return replay(existing, body)
    if not request.app.state.settings.conflicts_enabled:
        raise HTTPException(503, "CONFLICT_COMPARISON_DISABLED")
    for doc_id in sorted({r.document_id for r in runs.values()}, key=str):
        await db.scalar(
            select(Document)
            .where(Document.id == doc_id, Document.organization_id == org)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    left, lr = await source(db, org, runs[body.left_run_id])
    right, rr = await source(db, org, runs[body.right_run_id])
    assert lr.lease_extraction is not None and rr.lease_extraction is not None
    try:
        report = compare(lr.lease_extraction, rr.lease_extraction, left, right, utcnow())
    except ValueError as exc:
        raise HTTPException(409, "COMPARISON_RELATIONSHIP_OR_INPUT_UNSUPPORTED") from exc
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
        raise HTTPException(403, "COMPARISON_AUTHORIZATION_REVOKED")
    record = ConflictComparison(
        organization_id=org,
        left_run_id=left.run_id,
        right_run_id=right.run_id,
        left_document_id=left.document_id,
        left_version_id=left.version_id,
        right_document_id=right.document_id,
        right_version_id=right.version_id,
        requested_by=principal.user.id,
        auth_session_id=principal.session.id,
        request_key=body.request_key,
        left_input_sha256=report.left_input_sha256,
        right_input_sha256=report.right_input_sha256,
        report=report.model_dump(mode="json"),
    )
    try:
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(
            select(ConflictComparison).where(
                ConflictComparison.organization_id == org,
                ConflictComparison.request_key == body.request_key,
            )
        )
        if existing is not None:
            return replay(existing, body)
        raise HTTPException(409, "COMPARISON_SOURCE_OR_AUTH_CHANGED") from None

    from app.governance.service import audit

    await audit(
        db,
        org,
        "CONFLICT_COMPARED",
        "comparison",
        record.id,
        principal.user.id,
        request.state.request_id,
    )
    response = ComparisonOut.model_validate(record)
    await db.commit()
    return response


@router.get("/workflow-runs/{run_id}/conflict-comparisons", response_model=list[ComparisonOut])
async def history(
    run_id: UUID,
    principal: Admins,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConflictComparison]:
    await parent(db, principal.organization_id, run_id)
    rows = await db.scalars(
        select(ConflictComparison)
        .where(
            ConflictComparison.organization_id == principal.organization_id,
            or_(
                ConflictComparison.left_run_id == run_id, ConflictComparison.right_run_id == run_id
            ),
        )
        .order_by(ConflictComparison.created_at.desc(), ConflictComparison.id.desc())
        .limit(limit)
        .offset(offset)
    )
    visible = []
    for row in rows:
        try:
            await parent(db, principal.organization_id, row.left_run_id)
            await parent(db, principal.organization_id, row.right_run_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        visible.append(row)
    return visible


@router.get("/conflict-comparisons/{comparison_id}", response_model=ComparisonOut)
async def get(comparison_id: UUID, principal: Admins, db: Session) -> ConflictComparison:
    row = await db.scalar(
        select(ConflictComparison).where(
            ConflictComparison.id == comparison_id,
            ConflictComparison.organization_id == principal.organization_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Comparison not found")
    await parent(db, principal.organization_id, row.left_run_id)
    await parent(db, principal.organization_id, row.right_run_id)
    return row
