from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class RuleEvaluation(Base):
    """Append-only via API; scoped to an immutable workflow source and pinned rule snapshot."""

    __tablename__ = "rule_evaluations"
    __table_args__ = (
        UniqueConstraint("organization_id", "request_key", name="uq_rule_evaluation_request"),
        ForeignKeyConstraint(
            ["workflow_run_id", "document_id", "version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_rule_evaluation_workflow_source",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_rule_evaluation_actor",
        ),
        ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_rule_evaluation_session",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    workflow_run_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    auth_session_id: Mapped[UUID] = mapped_column(Uuid)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    input_sha256: Mapped[str] = mapped_column(String(64))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
