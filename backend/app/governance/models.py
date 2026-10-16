from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint("organization_id", "run_id", "field_index", name="uq_review_source_field"),
        UniqueConstraint("id", "organization_id", name="uq_review_tenant"),
        CheckConstraint("status IN ('OPEN','DECIDED')", name="review_status"),
        CheckConstraint("revision>=1 AND field_index>=0 AND field_index<24", name="review_bounds"),
        ForeignKeyConstraint(
            ["run_id", "document_id", "version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_review_source",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    field_index: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))
    original: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_review_single_decision"),
        UniqueConstraint("id", "organization_id", name="uq_review_decision_org"),
        UniqueConstraint("organization_id", "request_key", name="uq_review_decision_request"),
        CheckConstraint("action IN ('ACCEPT','MODIFY','REJECT')", name="review_action"),
        ForeignKeyConstraint(
            ["task_id", "organization_id"],
            ["review_tasks.id", "review_tasks.organization_id"],
            name="fk_decision_task",
        ),
        ForeignKeyConstraint(
            ["reviewer_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_decision_reviewer",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    task_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(2000))
    original: Mapped[dict[str, object]] = mapped_column(JSON)
    reviewed: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    """Retains opaque identity IDs after deletion; no cascading erase of evidence of actions."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "sequence", name="uq_audit_sequence"),
        UniqueConstraint("id", "organization_id", name="uq_audit_event_org"),
        CheckConstraint("sequence>0", name="audit_positive_sequence"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    event_type: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSON)
    previous_sha256: Mapped[str] = mapped_column(String(64))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
