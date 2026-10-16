from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ProcessingJob(Base):
    __tablename__ = "document_processing_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_processing_version"),
        CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="job_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="job_progress"),
        CheckConstraint("attempts >= 0", name="job_attempts"),
        ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_job_version_tenant",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_job_requester_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(40))
    parser_revision: Mapped[str] = mapped_column(String(50))
    page_number_kind: Mapped[str | None] = mapped_column(String(20))
    segment_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    character_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint("version_id", "page_number", name="uq_version_page"),
        UniqueConstraint(
            "version_id", "organization_id", "page_number", name="uq_page_tenant_number"
        ),
        ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_page_version_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    page_number: Mapped[int] = mapped_column(Integer)
    page_number_kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    headings: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    tables: Mapped[list[list[list[str]]]] = mapped_column(JSON)
    parser_revision: Mapped[str] = mapped_column(String(50))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "chunk_index", name="uq_version_chunk"),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="chunk_offsets"),
        ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_chunk_version_tenant",
        ),
        ForeignKeyConstraint(
            ["version_id", "organization_id", "page_number"],
            [
                "document_pages.version_id",
                "document_pages.organization_id",
                "document_pages.page_number",
            ],
            name="fk_chunk_page_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(150))
    text: Mapped[str] = mapped_column(Text)
    parser_revision: Mapped[str] = mapped_column(String(50))
