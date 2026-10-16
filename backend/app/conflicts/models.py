from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ConflictComparison(Base):
    """Append-only API snapshots of two explicitly selected scoped workflow sources."""

    __tablename__ = "conflict_comparisons"
    __table_args__ = (
        CheckConstraint("left_run_id <> right_run_id", name="conflict_distinct_runs"),
        CheckConstraint("left_version_id <> right_version_id", name="conflict_distinct_versions"),
        UniqueConstraint("organization_id", "request_key", name="uq_conflict_comparison_request"),
        ForeignKeyConstraint(
            ["left_run_id", "left_document_id", "left_version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_conflict_left_source",
        ),
        ForeignKeyConstraint(
            ["right_run_id", "right_document_id", "right_version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_conflict_right_source",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_conflict_comparison_actor",
        ),
        ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_conflict_comparison_session",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    left_document_id: Mapped[UUID] = mapped_column(Uuid)
    left_version_id: Mapped[UUID] = mapped_column(Uuid)
    left_run_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    right_document_id: Mapped[UUID] = mapped_column(Uuid)
    right_version_id: Mapped[UUID] = mapped_column(Uuid)
    right_run_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    auth_session_id: Mapped[UUID] = mapped_column(Uuid)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    left_input_sha256: Mapped[str] = mapped_column(String(64))
    right_input_sha256: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
