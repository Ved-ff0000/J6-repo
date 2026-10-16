"""Durable at-least-once delivery. Receiver must deduplicate delivery_id and check signature age."""

import asyncio
import sys
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import Organization, User
from app.auth.security import utcnow
from app.core.config import Settings
from app.db.session import create_engine
from app.documents.models import Document, DocumentVersion
from app.governance.models import AuditEvent, ReviewDecision, ReviewTask
from app.governance.service import audit
from app.operations.models import Delivery, LiveTicket, NotificationPreference
from app.operations.router import NOTIFY_TYPES
from app.operations.transports import DeliveryError, SignedWebhook, SMTPTransport


async def deadlines(factory: async_sessionmaker[AsyncSession]) -> None:
    today = utcnow().date()
    upper = today + timedelta(days=30)
    async with factory() as db, db.begin():
        literal = ReviewDecision.reviewed["normalized"]["iso_date"].as_string()
        already = exists(
            select(AuditEvent.id).where(
                AuditEvent.organization_id == ReviewDecision.organization_id,
                AuditEvent.resource_id == ReviewTask.id,
                AuditEvent.event_type == "DEADLINE_APPROACHING",
                AuditEvent.payload["today"].as_string() == today.isoformat(),
            )
        )
        rows = await db.execute(
            select(ReviewDecision, ReviewTask)
            .join(ReviewTask, ReviewTask.id == ReviewDecision.task_id)
            .join(Document, Document.id == ReviewTask.document_id)
            .join(DocumentVersion, DocumentVersion.id == ReviewTask.version_id)
            .join(Organization, Organization.id == ReviewDecision.organization_id)
            .where(
                ReviewDecision.organization_id == ReviewTask.organization_id,
                Document.organization_id == ReviewTask.organization_id,
                DocumentVersion.organization_id == ReviewTask.organization_id,
                Organization.status == "ACTIVE",
                Document.status != "ARCHIVED",
                DocumentVersion.version_number == Document.current_version,
                DocumentVersion.processing_status == "PARSED",
                ReviewDecision.action.in_(["ACCEPT", "MODIFY"]),
                ReviewDecision.reviewed["group"].as_string() == "dates",
                ReviewDecision.reviewed["field"].as_string() == "expiration",
                literal >= today.isoformat(),
                literal <= upper.isoformat(),
                ~already,
            )
            .order_by(ReviewDecision.organization_id, ReviewTask.id)
            .limit(100)
        )
        for decision, task in rows:
            await db.scalar(
                select(Organization.id)
                .where(Organization.id == decision.organization_id)
                .with_for_update()
            )
            duplicate = await db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.organization_id == decision.organization_id,
                    AuditEvent.resource_id == task.id,
                    AuditEvent.event_type == "DEADLINE_APPROACHING",
                    AuditEvent.payload["today"].as_string() == today.isoformat(),
                )
            )
            if duplicate is None:
                await audit(
                    db,
                    decision.organization_id,
                    "DEADLINE_APPROACHING",
                    "review",
                    task.id,
                    payload={
                        "today": today.isoformat(),
                        "basis": "Human-reviewed literal expiration, not established legal deadline",
                    },
                )


async def schedule(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db, db.begin():
        prefs = list(
            await db.scalars(
                select(NotificationPreference)
                .where(
                    or_(
                        NotificationPreference.email.is_(True),
                        NotificationPreference.webhook.is_(True),
                    )
                )
                .order_by(NotificationPreference.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        for pref in prefs:
            active = await db.scalar(
                select(User.id)
                .join(Organization, Organization.id == User.organization_id)
                .where(
                    User.id == pref.user_id,
                    User.organization_id == pref.organization_id,
                    User.status == "ACTIVE",
                    Organization.status == "ACTIVE",
                )
            )
            if active is None:
                continue
            rows = list(
                await db.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.organization_id == pref.organization_id,
                        AuditEvent.sequence > pref.after_sequence,
                    )
                    .order_by(AuditEvent.sequence)
                    .limit(100)
                )
            )
            for event in rows:
                if event.event_type not in NOTIFY_TYPES:
                    continue
                for channel, enabled in (("email", pref.email), ("webhook", pref.webhook)):
                    if enabled:
                        db.add(
                            Delivery(
                                organization_id=pref.organization_id,
                                user_id=pref.user_id,
                                event_id=event.id,
                                channel=channel,
                                next_attempt_at=utcnow(),
                            )
                        )
            if rows:
                pref.after_sequence = rows[-1].sequence
        await db.execute(
            delete(LiveTicket).where(LiveTicket.expires_at < utcnow() - timedelta(days=1))
        )


async def run_once(factory: async_sessionmaker[AsyncSession], settings: Settings) -> bool:
    await deadlines(factory)
    await schedule(factory)
    enabled = [
        channel
        for channel, value in (
            ("email", settings.email_enabled),
            ("webhook", settings.webhook_enabled),
        )
        if value
    ]
    if not enabled:
        return False
    async with factory() as db, db.begin():
        row = await db.scalar(
            select(Delivery)
            .where(
                Delivery.channel.in_(enabled),
                Delivery.attempts < 5,
                or_(
                    (Delivery.status == "PENDING") & (Delivery.next_attempt_at <= utcnow()),
                    (Delivery.status == "SENDING") & (Delivery.lease_expires_at < utcnow()),
                ),
            )
            .order_by(Delivery.next_attempt_at, Delivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        if (row.channel == "email" and not settings.email_enabled) or (
            row.channel == "webhook" and not settings.webhook_enabled
        ):
            return False
        user = await db.scalar(
            select(User)
            .join(Organization, Organization.id == User.organization_id)
            .where(
                User.id == row.user_id,
                User.organization_id == row.organization_id,
                User.status == "ACTIVE",
                Organization.status == "ACTIVE",
            )
        )
        pref = await db.scalar(
            select(NotificationPreference).where(
                NotificationPreference.organization_id == row.organization_id,
                NotificationPreference.user_id == row.user_id,
            )
        )
        if user is None or pref is None or not getattr(pref, row.channel):
            row.status = "FAILED"
            row.error_code = "RECIPIENT_REVOKED"
            return True
        event = await db.scalar(
            select(AuditEvent).where(
                AuditEvent.id == row.event_id, AuditEvent.organization_id == row.organization_id
            )
        )
        if event is None:
            row.status = "FAILED"
            row.error_code = "EVENT_UNAVAILABLE"
            return True
        row.status = "SENDING"
        row.attempts += 1
        row.claim_token = uuid4()
        row.lease_expires_at = utcnow() + timedelta(seconds=60)
        identity, token, channel, email = row.id, row.claim_token, row.channel, user.email
        payload: dict[str, object] = {
            "delivery_id": str(row.id),
            "event_type": event.event_type,
            "resource_id": str(event.resource_id),
            "organization_id": str(row.organization_id),
        }
    error = None
    try:
        if channel == "email":
            await SMTPTransport(settings).send(email, payload)
        else:
            await SignedWebhook(settings).send(payload, str(int(utcnow().timestamp())))
    except DeliveryError as exc:
        error = str(exc)
    except Exception:  # noqa: BLE001 - persist fixed transport error only
        error = "TRANSPORT_UNAVAILABLE"
    async with factory() as db, db.begin():
        row = await db.scalar(
            select(Delivery)
            .where(Delivery.id == identity, Delivery.claim_token == token)
            .with_for_update()
        )
        if row:
            row.status = "SENT" if error is None else "FAILED" if row.attempts >= 5 else "PENDING"
            row.error_code = error
            row.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 30 * 2**row.attempts))
            row.claim_token = None
            row.lease_expires_at = None
    return True


async def serve(once: bool = False) -> None:
    settings = Settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            worked = await run_once(factory, settings)
            if once:
                break
            if not worked:
                await asyncio.sleep(5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(serve("--once" in sys.argv))
