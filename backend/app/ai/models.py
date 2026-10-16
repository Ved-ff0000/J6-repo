from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ProviderRun(Base):
    """Diagnostic metadata only. Never stores prompts, generated text, credentials or documents."""

    __tablename__ = "provider_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "request_key", name="uq_provider_request_tenant"),
        CheckConstraint("status IN ('STARTED','SUCCEEDED','FAILED')", name="provider_run_status"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="provider_input_tokens"),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="provider_output_tokens"
        ),
        CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="provider_total_tokens"),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_provider_requester_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="STARTED")
    provider: Mapped[str] = mapped_column(String(30))
    profile: Mapped[str] = mapped_column(String(64))
    configured_model: Mapped[str] = mapped_column(String(120))
    returned_model: Mapped[str | None] = mapped_column(String(200))
    model_version: Mapped[str | None] = mapped_column(String(200))
    provider_response_id: Mapped[str | None] = mapped_column(String(200))
    prompt_name: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(40))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    temperature: Mapped[float] = mapped_column(Float)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(50))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
