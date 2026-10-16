from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.models import Timestamps
from app.db.models import Base


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    INDEXING = "INDEXING"
    READY = "READY"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class Document(Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_document_org"),
        CheckConstraint("current_version >= 1", name="document_positive_version"),
        ForeignKeyConstraint(
            ["uploaded_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_document_uploader_org",
        ),
        ForeignKeyConstraint(
            ["parent_document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_parent_org",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    uploaded_by: Mapped[UUID] = mapped_column(Uuid)
    parent_document_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(255))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, create_constraint=True, name="document_status"),
        default=DocumentStatus.UPLOADED,
    )
    classification: Mapped[str] = mapped_column(String(40), default="UNCLASSIFIED")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("id", "document_id", "organization_id", name="uq_version_document_tenant"),
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        UniqueConstraint("document_id", "checksum", name="uq_document_version_checksum"),
        CheckConstraint("version_number >= 1", name="version_positive_number"),
        CheckConstraint("file_size > 0", name="version_positive_size"),
        CheckConstraint("scan_status = 'NOT_SCANNED'", name="version_scan_status"),
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_version_document_org",
        ),
        ForeignKeyConstraint(
            ["uploaded_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_version_uploader_org",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    checksum: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(300), unique=True)
    uploaded_by: Mapped[UUID] = mapped_column(Uuid)
    page_count: Mapped[int | None] = mapped_column(Integer)
    processing_status: Mapped[str] = mapped_column(
        String(20), default="UPLOADED", server_default="UPLOADED"
    )
    parsed_segment_count: Mapped[int | None] = mapped_column(Integer)
    page_number_kind: Mapped[str | None] = mapped_column(String(20))
    scan_status: Mapped[str] = mapped_column(String(30), default="NOT_SCANNED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentMetadata(Base):
    """Explicitly user-supplied labels, never represented as AI extraction."""

    __tablename__ = "document_metadata"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_document_metadata"),
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_metadata_document_org",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_type: Mapped[str] = mapped_column(String(40), default="UNSPECIFIED")
    property_name: Mapped[str | None] = mapped_column(String(200))
    external_reference: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(String(2000))
