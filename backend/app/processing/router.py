from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.auth.dependencies import Principal, Session, require_permission
from app.core.errors import ErrorEnvelope
from app.documents.models import DocumentStatus, DocumentVersion
from app.documents.service import get_document
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.parsers import PARSER_REVISION

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["processing"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)},
)
Readers = Annotated[Principal, Depends(require_permission("documents:read"))]
Writers = Annotated[Principal, Depends(require_permission("documents:write"))]


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_id: UUID
    status: str
    stage: str
    progress: int
    attempts: int
    error_code: str | None
    parser_revision: str
    page_number_kind: str | None
    segment_count: int | None
    chunk_count: int | None
    character_count: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    page_number: int
    page_number_kind: str
    text: str
    headings: list[dict[str, object]]
    tables: list[list[list[str]]]
    parser_revision: str


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_id: UUID
    chunk_index: int
    page_number: int
    start_offset: int
    end_offset: int
    section: str | None
    text: str
    parser_revision: str


async def scoped_version(
    db: Session, organization_id: UUID, document_id: UUID, version_number: int
) -> DocumentVersion:
    await get_document(db, organization_id, document_id)
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(404, "Document version not found")
    return version


def check_policy(request: Request, version: DocumentVersion) -> None:
    settings = request.app.state.settings
    if not settings.processing_enabled:
        raise HTTPException(503, "Document processing is disabled")
    if version.scan_status != "CLEAN" and not settings.allow_unscanned_processing:
        raise HTTPException(
            409, "Unscanned processing is disabled; use synthetic development data only"
        )


@router.post(
    "/{document_id}/versions/{version_number}/process", response_model=JobOut, status_code=202
)
async def enqueue(
    document_id: UUID, version_number: int, request: Request, principal: Writers, db: Session
) -> JobOut:
    document = await get_document(db, principal.organization_id, document_id, lock=True)
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    check_policy(request, version)
    if document.status == DocumentStatus.ARCHIVED or version_number != document.current_version:
        raise HTTPException(409, "Only the latest non-archived version can be scheduled")
    existing = await db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.version_id == version.id,
            ProcessingJob.organization_id == principal.organization_id,
        )
    )
    if existing is not None:
        return JobOut.model_validate(existing)
    job = ProcessingJob(
        organization_id=principal.organization_id,
        document_id=document_id,
        version_id=version.id,
        requested_by=principal.user.id,
        parser_revision=PARSER_REVISION,
    )
    db.add(job)
    document.status = DocumentStatus.PROCESSING
    await db.flush()
    result = JobOut.model_validate(job)
    await db.commit()
    return result


@router.get("/{document_id}/versions/{version_number}/job", response_model=JobOut | None)
async def job_status(
    document_id: UUID, version_number: int, principal: Readers, db: Session
) -> ProcessingJob | None:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    return await db.scalar(
        select(ProcessingJob).where(
            ProcessingJob.version_id == version.id,
            ProcessingJob.organization_id == principal.organization_id,
        )
    )


@router.post(
    "/{document_id}/versions/{version_number}/retry", response_model=JobOut, status_code=202
)
async def retry(
    document_id: UUID, version_number: int, request: Request, principal: Writers, db: Session
) -> JobOut:
    document = await get_document(db, principal.organization_id, document_id, lock=True)
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    check_policy(request, version)
    job = await db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.version_id == version.id,
            ProcessingJob.organization_id == principal.organization_id,
        )
        .with_for_update()
    )
    if job is None:
        raise HTTPException(404, "Processing job not found")
    if (
        job.status != "FAILED"
        or job.attempts >= request.app.state.settings.processing_max_attempts
        or document.status == DocumentStatus.ARCHIVED
        or version_number != document.current_version
    ):
        raise HTTPException(
            409, "Job is not eligible for retry; upload a corrected new version if needed"
        )
    job.status, job.stage, job.progress = "QUEUED", "QUEUED", 0
    job.error_code, job.finished_at = None, None
    job.requested_by = principal.user.id
    document.status, version.processing_status = DocumentStatus.PROCESSING, "UPLOADED"
    await db.flush()
    result = JobOut.model_validate(job)
    await db.commit()
    return result


@router.get("/{document_id}/versions/{version_number}/pages", response_model=list[PageOut])
async def pages(
    document_id: UUID,
    version_number: int,
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentPage]:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    return list(
        (
            await db.scalars(
                select(DocumentPage)
                .where(
                    DocumentPage.version_id == version.id,
                    DocumentPage.organization_id == principal.organization_id,
                )
                .order_by(DocumentPage.page_number)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.get("/{document_id}/versions/{version_number}/chunks", response_model=list[ChunkOut])
async def chunks(
    document_id: UUID,
    version_number: int,
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentChunk]:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    return list(
        (
            await db.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.version_id == version.id,
                    DocumentChunk.organization_id == principal.organization_id,
                )
                .order_by(DocumentChunk.chunk_index)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
