import hashlib
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Principal
from app.documents.models import Document, DocumentMetadata, DocumentStatus, DocumentVersion
from app.documents.schemas import DocumentOut, MetadataOut, VersionOut
from app.documents.storage import ObjectStore, StorageUnavailable
from app.documents.validation import ValidatedUpload

logger = logging.getLogger("leaseguard.documents")


async def get_document(
    db: AsyncSession, organization_id: UUID, document_id: UUID, *, lock: bool = False
) -> Document:
    query = select(Document).where(
        Document.id == document_id, Document.organization_id == organization_id
    )
    if lock:
        query = query.with_for_update()
    document = await db.scalar(query)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


async def describe(db: AsyncSession, document: Document) -> DocumentOut:
    metadata = await db.scalar(
        select(DocumentMetadata).where(
            DocumentMetadata.document_id == document.id,
            DocumentMetadata.organization_id == document.organization_id,
        )
    )
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.organization_id == document.organization_id,
            DocumentVersion.version_number == document.current_version,
        )
    )
    if metadata is None or version is None:
        raise HTTPException(503, "Document metadata is unavailable")
    return serialize(document, metadata, version)


def serialize(
    document: Document, metadata: DocumentMetadata, version: DocumentVersion
) -> DocumentOut:
    return DocumentOut(
        **{
            key: getattr(document, key)
            for key in DocumentOut.model_fields
            if key not in {"metadata", "latest_version"}
        },
        metadata=MetadataOut.model_validate(metadata),
        latest_version=VersionOut.model_validate(version),
    )


async def persist_upload(
    db: AsyncSession,
    store: ObjectStore,
    principal: Principal,
    upload: ValidatedUpload,
    *,
    document: Document,
    is_new: bool,
    metadata: DocumentMetadata | None = None,
) -> DocumentOut:
    organization_id = str(principal.organization_id)
    document_id = str(document.id)
    commit_started = False
    write_completed = False
    version_id = uuid4()
    key = f"{principal.organization_id}/documents/{document.id}/versions/{version_id}"
    # A unique key per attempt allows compensation without touching any older bytes.
    try:
        await store.put(key, upload.content, upload.mime_type)
        write_completed = True
        if is_new:
            db.add(document)
            await db.flush()
            if metadata is not None:
                db.add(metadata)
        db.add(
            DocumentVersion(
                id=version_id,
                organization_id=principal.organization_id,
                document_id=document.id,
                version_number=document.current_version,
                filename=upload.filename,
                mime_type=upload.mime_type,
                checksum=upload.checksum,
                file_size=len(upload.content),
                object_key=key,
                uploaded_by=principal.user.id,
            )
        )
        await db.flush()
        # SQLAlchemy onupdate timestamps may require an explicit refresh in async mode.
        await db.refresh(document)
        result = await describe(db, document)
        # Commit BEFORE returning success; request teardown must not fail after a success response.
        from app.governance.service import audit

        await audit(
            db,
            principal.organization_id,
            "DOCUMENT_UPLOADED",
            "document",
            document.id,
            principal.user.id,
            payload={
                "version_id": str(version_id),
                "version_number": document.current_version,
                "bytes": len(upload.content),
            },
        )
        commit_started = True
        await db.commit()
        return result
    except Exception:
        try:
            await db.rollback()
        except SQLAlchemyError:
            logger.error(
                "upload_rollback_failed",
                extra={
                    "organization_id": organization_id,
                    "document_id": document_id,
                    "version_id": str(version_id),
                },
            )
        try:
            if not commit_started and write_completed:
                await store.delete(key)
            elif commit_started:
                logger.error(
                    "upload_commit_outcome_uncertain",
                    extra={
                        "organization_id": organization_id,
                        "document_id": document_id,
                        "version_id": str(version_id),
                    },
                )
            elif not write_completed:
                logger.error(
                    "upload_write_outcome_uncertain",
                    extra={
                        "organization_id": organization_id,
                        "document_id": document_id,
                        "version_id": str(version_id),
                    },
                )
        except StorageUnavailable:
            # Unavoidable object/DB distributed-transaction gap: retain for operator reconciliation.
            logger.error(
                "upload_compensation_failed",
                extra={
                    "organization_id": organization_id,
                    "document_id": document_id,
                    "version_id": str(version_id),
                },
            )
        raise


async def verify_download(store: ObjectStore, version: DocumentVersion) -> bytes:
    content = await store.get(version.object_key, version.file_size)
    if len(content) != version.file_size or hashlib.sha256(content).hexdigest() != version.checksum:
        raise StorageUnavailable("Stored document integrity check failed")
    return content


def require_editable(document: Document) -> None:
    if document.status not in {
        DocumentStatus.UPLOADED,
        DocumentStatus.PARSED,
        DocumentStatus.FAILED,
    }:
        raise HTTPException(409, "Documents cannot be edited while processing or archived")
