from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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


class IndexJob(Base):
    __tablename__ = "document_index_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_index_version"),
        CheckConstraint("status IN ('QUEUED','RUNNING','READY','FAILED')", name="index_status"),
        CheckConstraint("attempts >= 0", name="index_attempts"),
        ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_index_version_tenant",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_index_requester_tenant",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    profile: Mapped[str] = mapped_column(String(64))
    generation: Mapped[UUID | None] = mapped_column(Uuid)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(40))
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
