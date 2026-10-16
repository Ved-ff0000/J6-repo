from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index(
            "uq_workflow_run_source",
            "id",
            "document_id",
            "version_id",
            "organization_id",
            unique=True,
        ),
        UniqueConstraint("organization_id", "request_key", name="uq_workflow_request_tenant"),
        CheckConstraint("extraction_mode IN ('CANDIDATES','LEASE_FIELDS')", name="workflow_mode"),
        CheckConstraint("status IN ('STARTED','REVIEW_REQUIRED','FAILED')", name="workflow_status"),
        ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_workflow_version_tenant",
        ),
        ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_workflow_session_tenant",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_workflow_requester_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    auth_session_id: Mapped[UUID] = mapped_column(Uuid)
    document_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    extraction_mode: Mapped[str] = mapped_column(
        String(30), default="CANDIDATES", server_default="CANDIDATES"
    )
    revision: Mapped[str] = mapped_column(String(50))
    source_checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="STARTED")
    stage: Mapped[str] = mapped_column(String(40), default="STARTED")
    trace: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    calls: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
