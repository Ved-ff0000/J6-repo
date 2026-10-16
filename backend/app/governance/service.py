from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Organization
from app.auth.security import as_utc, utcnow
from app.governance.models import AuditEvent, ReviewTask
from app.rules.engine import fingerprint, input_hash
from app.workflows.models import WorkflowRun
from app.workflows.schemas import WorkflowResult

ZERO = "0" * 64


def event_data(row: AuditEvent) -> dict[str, object]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id),
        "sequence": row.sequence,
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "event_type": row.event_type,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id),
        "request_id": row.request_id,
        "payload": row.payload,
        "previous_sha256": row.previous_sha256,
        "created_at": as_utc(row.created_at).isoformat(),
    }


async def audit(
    db: AsyncSession,
    org: UUID,
    event: str,
    kind: str,
    resource: UUID,
    actor: UUID | None = None,
    request_id: str = "system",
    payload: dict[str, object] | None = None,
) -> AuditEvent:
    # The org row serializes chain appends across all API/worker processes on PostgreSQL.
    await db.scalar(select(Organization.id).where(Organization.id == org).with_for_update())
    await db.flush()
    previous = await db.scalar(
        select(AuditEvent)
        .where(AuditEvent.organization_id == org)
        .order_by(AuditEvent.sequence.desc())
        .limit(1)
    )
    row = AuditEvent(
        id=uuid4(),
        organization_id=org,
        sequence=previous.sequence + 1 if previous else 1,
        actor_id=actor,
        event_type=event,
        resource_type=kind,
        resource_id=resource,
        request_id=request_id[:64],
        payload=payload or {},
        previous_sha256=previous.sha256 if previous else ZERO,
        created_at=utcnow(),
    )
    row.sha256 = fingerprint(event_data(row))
    db.add(row)
    await db.flush()
    return row


async def enqueue_reviews(db: AsyncSession, run: WorkflowRun) -> list[ReviewTask]:
    if run.status != "REVIEW_REQUIRED" or not run.result:
        return []
    result = WorkflowResult.model_validate(run.result)
    if result.lease_extraction is None:
        return []
    existing = set(
        await db.scalars(
            select(ReviewTask.field_index).where(
                ReviewTask.organization_id == run.organization_id, ReviewTask.run_id == run.id
            )
        )
    )
    tasks = []
    for index, field in enumerate(result.lease_extraction.fields):
        if index in existing:
            continue
        task = ReviewTask(
            id=uuid4(),
            organization_id=run.organization_id,
            run_id=run.id,
            document_id=run.document_id,
            version_id=run.version_id,
            field_index=index,
            source_sha256=input_hash(result.lease_extraction),
            original=field.model_dump(mode="json"),
        )
        db.add(task)
        tasks.append(task)
    if tasks:
        await audit(
            db,
            run.organization_id,
            "REVIEW_CREATED",
            "workflow",
            run.id,
            payload={"tasks": len(tasks)},
        )
    return tasks
