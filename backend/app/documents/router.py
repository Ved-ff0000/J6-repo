from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, select

from app.auth.dependencies import PERMISSIONS, Principal, Session, require_permission
from app.auth.security import utcnow
from app.core.errors import ErrorEnvelope
from app.documents import service
from app.documents.models import Document, DocumentMetadata, DocumentStatus, DocumentVersion
from app.documents.schemas import (
    DocumentOut,
    DocumentPage,
    DocumentType,
    DocumentUpdate,
    UploadPolicy,
    VersionOut,
)
from app.documents.storage import ObjectStore
from app.documents.validation import read_upload

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 413, 415, 422, 503)},
)
Readers = Annotated[Principal, Depends(require_permission("documents:read"))]
Writers = Annotated[Principal, Depends(require_permission("documents:write"))]


def get_store(request: Request) -> ObjectStore:
    return request.app.state.object_store  # type: ignore[no-any-return]


Store = Annotated[ObjectStore, Depends(get_store)]


@router.get("/policy", response_model=UploadPolicy)
async def upload_policy(request: Request, principal: Readers) -> UploadPolicy:
    return UploadPolicy(
        max_upload_bytes=request.app.state.settings.max_upload_bytes,
        allowed_extensions=[".pdf", ".docx", ".txt"],
        can_upload=principal.user.role in PERMISSIONS["documents:write"],
        parsing_enabled=request.app.state.settings.processing_enabled,
    )


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    principal: Writers,
    db: Session,
    store: Store,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    document_type: Annotated[DocumentType, Form()] = "UNSPECIFIED",
    property_name: Annotated[str | None, Form(max_length=200)] = None,
    external_reference: Annotated[str | None, Form(max_length=120)] = None,
    notes: Annotated[str | None, Form(max_length=2000)] = None,
    parent_document_id: Annotated[UUID | None, Form()] = None,
) -> DocumentOut:
    """Retain an original; validation is not malware clearance, parsing or AI extraction."""
    if parent_document_id is not None:
        await service.get_document(db, principal.organization_id, parent_document_id)
    upload = await read_upload(file, request.app.state.settings.max_upload_bytes)
    clean_title = title.strip() if title is not None else upload.filename
    if not clean_title:
        raise HTTPException(422, "Title must not be blank")
    document = Document(
        id=uuid4(),
        organization_id=principal.organization_id,
        uploaded_by=principal.user.id,
        parent_document_id=parent_document_id,
        title=clean_title,
        current_version=1,
        status=DocumentStatus.UPLOADED,
    )
    metadata = DocumentMetadata(
        document_id=document.id,
        organization_id=principal.organization_id,
        document_type=document_type,
        property_name=property_name,
        external_reference=external_reference,
        notes=notes,
    )
    return await service.persist_upload(
        db, store, principal, upload, document=document, is_new=True, metadata=metadata
    )


@router.get("", response_model=DocumentPage)
async def list_documents(
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: DocumentStatus | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> DocumentPage:
    filters = [Document.organization_id == principal.organization_id]
    if status is not None:
        filters.append(Document.status == status)
    if q:
        # autoescape makes wildcard characters literal; all values remain bound parameters.
        filters.append(Document.title.icontains(q, autoescape=True))
    total = await db.scalar(select(func.count()).select_from(Document).where(*filters))
    documents = (
        await db.execute(
            select(Document, DocumentMetadata, DocumentVersion)
            .join(
                DocumentMetadata,
                (DocumentMetadata.document_id == Document.id)
                & (DocumentMetadata.organization_id == Document.organization_id),
            )
            .join(
                DocumentVersion,
                (DocumentVersion.document_id == Document.id)
                & (DocumentVersion.organization_id == Document.organization_id)
                & (DocumentVersion.version_number == Document.current_version),
            )
            .where(*filters)
            .order_by(Document.created_at.desc(), Document.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return DocumentPage(
        items=[
            service.serialize(document, metadata, version)
            for document, metadata, version in documents
        ],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def document_detail(document_id: UUID, principal: Readers, db: Session) -> DocumentOut:
    result = await service.describe(
        db, await service.get_document(db, principal.organization_id, document_id)
    )
    from app.governance.service import audit

    await audit(
        db, principal.organization_id, "DOCUMENT_VIEWED", "document", document_id, principal.user.id
    )
    await db.commit()
    return result


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: UUID, data: DocumentUpdate, principal: Writers, db: Session
) -> DocumentOut:
    document = await service.get_document(db, principal.organization_id, document_id, lock=True)
    service.require_editable(document)
    metadata = await db.scalar(
        select(DocumentMetadata).where(
            DocumentMetadata.document_id == document.id,
            DocumentMetadata.organization_id == principal.organization_id,
        )
    )
    if metadata is None:
        raise HTTPException(503, "Document metadata is unavailable")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(document if key == "title" else metadata, key, value)
    document.updated_at = utcnow()
    await db.flush()
    await db.refresh(document)
    result = await service.describe(db, document)
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "DOCUMENT_METADATA_UPDATED",
        "document",
        document.id,
        principal.user.id,
    )
    await db.commit()
    return result


@router.get("/{document_id}/versions", response_model=list[VersionOut])
async def versions(
    document_id: UUID,
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DocumentVersion]:
    await service.get_document(db, principal.organization_id, document_id)
    return list(
        (
            await db.scalars(
                select(DocumentVersion)
                .where(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.organization_id == principal.organization_id,
                )
                .order_by(DocumentVersion.version_number.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.post("/{document_id}/versions", response_model=DocumentOut, status_code=201)
async def upload_version(
    document_id: UUID,
    request: Request,
    principal: Writers,
    db: Session,
    store: Store,
    file: Annotated[UploadFile, File()],
    expected_version: Annotated[int, Form(ge=1)],
) -> DocumentOut:
    document = await service.get_document(db, principal.organization_id, document_id, lock=True)
    service.require_editable(document)
    if document.current_version != expected_version:
        raise HTTPException(409, "The current version changed; reload before uploading")
    upload = await read_upload(file, request.app.state.settings.max_upload_bytes)
    if await db.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.checksum == upload.checksum,
        )
    ):
        raise HTTPException(409, "Identical content already exists in this document's history")
    document.current_version += 1
    document.status = DocumentStatus.UPLOADED
    return await service.persist_upload(
        db, store, principal, upload, document=document, is_new=False
    )


@router.get("/{document_id}/versions/{version_number}/download")
async def download(
    document_id: UUID, version_number: int, principal: Readers, db: Session, store: Store
) -> Response:
    await service.get_document(db, principal.organization_id, document_id)
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(404, "Document version not found")
    content = await service.verify_download(store, version)
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "DOCUMENT_DOWNLOADED",
        "document",
        document_id,
        principal.user.id,
        payload={"version_id": str(version.id)},
    )
    await db.commit()
    # Always attachment, including PDFs/TXT: never render untrusted bytes as active same-origin content.
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "no-store",
        },
    )


@router.post("/{document_id}/archive", response_model=DocumentOut)
async def archive(document_id: UUID, principal: Writers, db: Session) -> DocumentOut:
    document = await service.get_document(db, principal.organization_id, document_id, lock=True)
    if document.status not in {
        DocumentStatus.UPLOADED,
        DocumentStatus.PARSED,
        DocumentStatus.FAILED,
        DocumentStatus.ARCHIVED,
    }:
        raise HTTPException(409, "Cannot archive a processing document")
    document.status = DocumentStatus.ARCHIVED
    document.updated_at = utcnow()
    await db.flush()
    await db.refresh(document)
    result = await service.describe(db, document)
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "DOCUMENT_ARCHIVED",
        "document",
        document.id,
        principal.user.id,
    )
    await db.commit()
    return result


@router.post("/{document_id}/restore", response_model=DocumentOut)
async def restore(document_id: UUID, principal: Writers, db: Session) -> DocumentOut:
    document = await service.get_document(db, principal.organization_id, document_id, lock=True)
    if document.status != DocumentStatus.ARCHIVED:
        raise HTTPException(409, "Document is not archived")
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.version_number == document.current_version,
        )
    )
    document.status = (
        DocumentStatus(version.processing_status)
        if version is not None
        else DocumentStatus.UPLOADED
    )
    document.updated_at = utcnow()
    await db.flush()
    await db.refresh(document)
    result = await service.describe(db, document)
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "DOCUMENT_RESTORED",
        "document",
        document.id,
        principal.user.id,
    )
    await db.commit()
    return result
