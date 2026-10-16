import asyncio
import hashlib
import math
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal, Session, require_permission
from app.core.config import Settings
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentMetadata, DocumentStatus, DocumentVersion
from app.documents.service import get_document
from app.processing.models import DocumentChunk, DocumentPage
from app.processing.router import scoped_version
from app.search.embeddings import Embedder, SearchFailure
from app.search.models import IndexJob
from app.search.store import VectorStore, point_id
from app.search.worker import check_source

router = APIRouter(
    prefix="/api/v1",
    tags=["retrieval"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)},
)
Readers = Annotated[Principal, Depends(require_permission("documents:read"))]
Writers = Annotated[Principal, Depends(require_permission("documents:write"))]


class IndexOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    attempts: int
    profile: str
    chunk_count: int | None
    error_code: str | None
    finished_at: datetime | None


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    document_id: UUID | None = None
    version_number: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1, max_length=150)
    document_type: str | None = Field(default=None, min_length=1, max_length=40)
    property_name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def explicit_history(self) -> "SearchIn":
        if self.version_number is not None and self.document_id is None:
            raise ValueError("Historical searches require a document ID")
        if not self.query.strip() or not re.search(r"\w", self.query):
            raise ValueError("Query must not be blank")
        return self


class Citation(BaseModel):
    chunk_id: UUID
    document_id: UUID
    document_title: str
    version_id: UUID
    version_number: int
    page: int
    page_number_kind: str
    section: str | None
    start_offset: int
    end_offset: int
    evidence: str
    parser_revision: str
    document_type: str
    property_name: str | None
    original_url: str


class Hit(BaseModel):
    score: float
    citation: Citation


class SearchOut(BaseModel):
    embedding: str
    profile: str
    searched_versions: int
    hits: list[Hit]


def enabled(request: Request) -> None:
    if not request.app.state.settings.retrieval_enabled:
        raise HTTPException(503, "Vector retrieval is disabled")


@router.get("/search/config")
async def configuration(request: Request, principal: Readers) -> dict[str, object]:
    embedder = Embedder(request.app.state.settings)
    return {
        "enabled": request.app.state.settings.retrieval_enabled,
        "embedding": embedder.label,
        "profile": embedder.profile,
    }


@router.post(
    "/documents/{document_id}/versions/{version_number}/index",
    response_model=IndexOut,
    status_code=202,
)
async def enqueue(
    document_id: UUID,
    version_number: int,
    request: Request,
    principal: Writers,
    db: Session,
    rebuild: bool = False,
) -> IndexOut:
    document = await get_document(db, principal.organization_id, document_id, lock=True)
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    enabled(request)
    settings = request.app.state.settings
    if (
        document.status == DocumentStatus.ARCHIVED
        or document.current_version != version_number
        or version.processing_status != "PARSED"
    ):
        raise HTTPException(409, "Only the latest parsed, non-archived version can be indexed")
    if version.scan_status != "CLEAN" and not settings.allow_unscanned_processing:
        raise HTTPException(409, "Unscanned indexing is disabled")
    profile = Embedder(settings).profile
    job = await db.scalar(
        select(IndexJob)
        .where(
            IndexJob.version_id == version.id, IndexJob.organization_id == principal.organization_id
        )
        .with_for_update()
    )
    if job is not None:
        if job.status in ("QUEUED", "RUNNING") or (
            job.status == "READY" and job.profile == profile and not rebuild
        ):
            return IndexOut.model_validate(job)
        if job.status == "READY" and rebuild:
            job.attempts = 0
        if job.profile == profile and job.attempts >= settings.index_max_attempts:
            raise HTTPException(
                409, "Index retry limit reached; operator remediation or a new version is required"
            )
        if job.profile != profile:
            job.attempts = 0
        job.status, job.profile, job.error_code, job.finished_at = "QUEUED", profile, None, None
        job.requested_by = principal.user.id
        job.generation = None
        job.chunk_count = None
        job.lease_expires_at = None
    else:
        job = IndexJob(
            organization_id=principal.organization_id,
            document_id=document_id,
            version_id=version.id,
            requested_by=principal.user.id,
            profile=profile,
        )
        db.add(job)
    await db.flush()
    from app.governance.service import audit

    await audit(
        db, principal.organization_id, "INDEX_REQUESTED", "index_job", job.id, principal.user.id
    )
    result = IndexOut.model_validate(job)
    await db.commit()
    return result


@router.get(
    "/documents/{document_id}/versions/{version_number}/index", response_model=IndexOut | None
)
async def index_status(
    document_id: UUID, version_number: int, principal: Readers, db: Session
) -> IndexJob | None:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    return await db.scalar(
        select(IndexJob).where(
            IndexJob.version_id == version.id, IndexJob.organization_id == principal.organization_id
        )
    )


async def citation(db: Session, organization_id: UUID, chunk_id: UUID) -> Citation:
    row = (
        await db.execute(
            select(DocumentChunk, DocumentPage, Document, DocumentVersion, DocumentMetadata)
            .join(
                DocumentPage,
                and_(
                    DocumentPage.version_id == DocumentChunk.version_id,
                    DocumentPage.organization_id == DocumentChunk.organization_id,
                    DocumentPage.page_number == DocumentChunk.page_number,
                ),
            )
            .join(
                Document,
                and_(
                    Document.id == DocumentChunk.document_id,
                    Document.organization_id == DocumentChunk.organization_id,
                ),
            )
            .join(
                DocumentVersion,
                and_(
                    DocumentVersion.id == DocumentChunk.version_id,
                    DocumentVersion.organization_id == DocumentChunk.organization_id,
                ),
            )
            .join(
                DocumentMetadata,
                and_(
                    DocumentMetadata.document_id == Document.id,
                    DocumentMetadata.organization_id == organization_id,
                ),
            )
            .where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.organization_id == organization_id,
                Document.status != DocumentStatus.ARCHIVED,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Citation not found")
    chunk, page, document, version, metadata = row
    try:
        check_source(chunk, page)
    except SearchFailure as exc:
        raise HTTPException(409, "Source integrity check failed") from exc
    return Citation(
        chunk_id=chunk.id,
        document_id=document.id,
        document_title=document.title,
        version_id=version.id,
        version_number=version.version_number,
        page=page.page_number,
        page_number_kind=page.page_number_kind,
        section=chunk.section,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        evidence=chunk.text,
        parser_revision=chunk.parser_revision,
        document_type=metadata.document_type,
        property_name=metadata.property_name,
        original_url=f"/api/v1/documents/{document.id}/versions/{version.version_number}/download",
    )


@router.get("/search/citations/{chunk_id}", response_model=Citation)
async def get_citation(chunk_id: UUID, principal: Readers, db: Session) -> Citation:
    return await citation(db, principal.organization_id, chunk_id)


@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, request: Request, principal: Readers, db: Session) -> SearchOut:
    return await retrieve(
        body,
        request.app.state.settings,
        request.app.state.vector_store,
        principal.organization_id,
        db,
    )


async def retrieve(
    body: SearchIn, settings: Settings, store: VectorStore, organization_id: UUID, db: AsyncSession
) -> SearchOut:
    # Validate any explicit object scope before probing external systems.
    if body.document_id:
        await get_document(db, organization_id, body.document_id)
        if body.version_number:
            await scoped_version(db, organization_id, body.document_id, body.version_number)
    if not settings.retrieval_enabled:
        raise HTTPException(503, "Vector retrieval is disabled")
    embedder = Embedder(settings)
    query = (
        select(IndexJob, DocumentVersion)
        .join(
            DocumentVersion,
            and_(
                DocumentVersion.id == IndexJob.version_id,
                DocumentVersion.organization_id == organization_id,
            ),
        )
        .join(
            Document,
            and_(
                Document.id == IndexJob.document_id,
                Document.organization_id == organization_id,
            ),
        )
        .join(
            DocumentMetadata,
            and_(
                DocumentMetadata.document_id == Document.id,
                DocumentMetadata.organization_id == organization_id,
            ),
        )
        .where(
            IndexJob.organization_id == organization_id,
            IndexJob.status == "READY",
            IndexJob.profile == embedder.profile,
            Document.status != DocumentStatus.ARCHIVED,
        )
    )
    query = (
        query.where(DocumentVersion.version_number == body.version_number)
        if body.version_number
        else query.where(DocumentVersion.version_number == Document.current_version)
    )
    if body.document_id:
        query = query.where(Document.id == body.document_id)
    if body.document_type:
        query = query.where(DocumentMetadata.document_type == body.document_type)
    if body.property_name:
        query = query.where(DocumentMetadata.property_name == body.property_name)
    rows = (await db.execute(query.order_by(IndexJob.id).limit(201))).all()
    if len(rows) > 200:
        raise HTTPException(
            422, "Narrow filters: at most 200 indexed versions may be searched per request"
        )
    allowed = {
        str(job.generation): (job, version) for job, version in rows if job.generation is not None
    }
    if not allowed:
        return SearchOut(
            embedding=embedder.label, profile=embedder.profile, searched_versions=0, hits=[]
        )
    try:
        async with asyncio.timeout(45):
            vector = (await embedder.embed([body.query]))[0]
            points = await store.search(
                vector,
                organization_id,
                list(allowed),
                limit=min(body.top_k * 4, 80),
                page=body.page,
                section=body.section,
            )
    except SearchFailure as exc:
        raise HTTPException(503, exc.code) from exc
    except TimeoutError as exc:
        raise HTTPException(503, "RETRIEVAL_TIMEOUT") from exc
    hits = []
    seen = set()
    for point in points:
        payload = point.payload or {}
        try:
            job, version = allowed[str(payload["generation"])]
            chunk_id = UUID(str(payload["chunk_id"]))
            if (
                job.generation is None
                or str(point.id) != point_id(job.generation, chunk_id)
                or payload.get("organization_id") != str(organization_id)
                or payload.get("profile") != embedder.profile
                or payload.get("version_id") != str(version.id)
            ):
                continue
            # Recheck current SQL eligibility after external I/O; archive, version,
            # metadata and publication changes must not expose a stale snapshot.
            if (
                await db.scalar(
                    query.with_only_columns(IndexJob.id).where(
                        IndexJob.id == job.id,
                        IndexJob.generation == UUID(str(payload["generation"])),
                    )
                )
                is None
            ):
                continue
            source = await citation(db, organization_id, chunk_id)
            if (
                source.version_id != version.id
                or source.document_id != job.document_id
                or payload.get("source_hash")
                != hashlib.sha256(source.evidence.encode()).hexdigest()
                or payload.get("parser_revision") != source.parser_revision
            ):
                continue
            if body.page is not None and source.page != body.page:
                continue
            if body.section is not None and source.section != body.section:
                continue
            if source.chunk_id in seen or not math.isfinite(point.score):
                continue
            hits.append(Hit(score=point.score, citation=source))
            seen.add(source.chunk_id)
            if len(hits) >= body.top_k:
                break
        except (KeyError, TypeError, ValueError, HTTPException):
            continue
    return SearchOut(
        embedding=embedder.label,
        profile=embedder.profile,
        searched_versions=len(allowed),
        hits=hits,
    )
