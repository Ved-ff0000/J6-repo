from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
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


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_notification_preferences"),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_notification_user",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook: Mapped[bool] = mapped_column(Boolean, default=False)
    after_sequence: Mapped[int] = mapped_column(Integer, default=0)


class NotificationReceipt(Base):
    __tablename__ = "notification_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_notification_receipt"),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_receipt_user",
        ),
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["audit_events.id", "audit_events.organization_id"],
            name="fk_receipt_event",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Delivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", "channel", name="uq_delivery_event_user_channel"),
        CheckConstraint("channel IN ('email','webhook')", name="delivery_channel"),
        CheckConstraint("status IN ('PENDING','SENDING','SENT','FAILED')", name="delivery_status"),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_delivery_user",
        ),
        ForeignKeyConstraint(
            ["event_id", "organization_id"],
            ["audit_events.id", "audit_events.organization_id"],
            name="fk_delivery_event",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now().astimezone()
    )
    claim_token: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationRun(Base):
    __tablename__ = "integration_runs"
    __table_args__ = (
        UniqueConstraint("organization_id", "request_key", name="uq_integration_request"),
        ForeignKeyConstraint(
            ["decision_id", "organization_id"],
            ["review_decisions.id", "review_decisions.organization_id"],
            name="fk_integration_decision",
        ),
        ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_integration_user",
        ),
        CheckConstraint(
            "target IN ('salesforce_mock','sap_mock','servicenow_mock')",
            name="integration_mock_target",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    decision_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    request_key: Mapped[UUID] = mapped_column(Uuid)
    target: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="SIMULATED")
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LiveTicket(Base):
    __tablename__ = "live_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_live_session",
        ),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_live_user",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    session_id: Mapped[UUID] = mapped_column(Uuid)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
