"""Durable source-backed graph projection; SQL publication fences stale generations."""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import PERMISSIONS
from app.auth.models import Organization, User
from app.auth.security import as_utc, utcnow
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.documents.models import Document, DocumentStatus, DocumentVersion
from app.graph.models import GraphSnapshot
from app.graph.service import GraphFailure, latest, verify
from app.graph.store import GraphStore, Scope

logger = logging.getLogger("leaseguard.graph")


@dataclass(frozen=True)
class Claim:
    id: UUID
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    generation: UUID

    def scope(self) -> Scope:
        return Scope(
            self.organization_id, self.document_id, self.version_id, self.id, self.generation
        )


async def authorized(db: AsyncSession, snapshot: GraphSnapshot) -> bool:
    actor = await db.scalar(
        select(User).where(
            User.id == snapshot.requested_by, User.organization_id == snapshot.organization_id
        )
    )
    org = await db.get(Organization, snapshot.organization_id)
    return bool(
        actor is not None
        and actor.status == "ACTIVE"
        and actor.role in PERMISSIONS["documents:write"]
        and org is not None
        and org.status == "ACTIVE"
    )


async def policy(
    db: AsyncSession, document: Document, snapshot: GraphSnapshot, settings: Settings
) -> str | None:
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == snapshot.version_id,
            DocumentVersion.organization_id == snapshot.organization_id,
            DocumentVersion.document_id == snapshot.document_id,
        )
    )
    current = await latest(db, snapshot.organization_id, snapshot.version_id)
    if current is None or current.id != snapshot.id:
        return "SUPERSEDED"
    if (
        version is None
        or version.processing_status != "PARSED"
        or version.version_number != document.current_version
        or document.status == DocumentStatus.ARCHIVED
    ):
        return "VERSION_CHANGED"
    if version.scan_status != "CLEAN" and not settings.allow_unscanned_processing:
        return "POLICY_BLOCKED"
    if not await authorized(db, snapshot):
        return "AUTHORIZATION_REVOKED"
    return None


async def claim_next(factory: async_sessionmaker[AsyncSession], settings: Settings) -> Claim | None:
    if not settings.graph_enabled:
        return None
    eligible = or_(
        GraphSnapshot.status == "QUEUED",
        and_(GraphSnapshot.status == "RUNNING", GraphSnapshot.lease_expires_at < utcnow()),
    )
    async with factory() as db, db.begin():
        candidates = (
            await db.execute(
                select(GraphSnapshot.id, GraphSnapshot.document_id, GraphSnapshot.organization_id)
                .where(eligible)
                .order_by(GraphSnapshot.created_at, GraphSnapshot.revision, GraphSnapshot.id)
                .limit(20)
            )
        ).all()
        for identifier, document_id, organization_id in candidates:
            document = await db.scalar(
                select(Document)
                .where(Document.id == document_id, Document.organization_id == organization_id)
                .with_for_update(skip_locked=True)
            )
            if document is None:
                continue
            snapshot = await db.scalar(
                select(GraphSnapshot)
                .where(GraphSnapshot.id == identifier, eligible)
                .with_for_update(skip_locked=True)
            )
            if snapshot is None:
                continue
            error = (
                "WORKER_LOST"
                if snapshot.attempts >= settings.graph_max_attempts
                else await policy(db, document, snapshot, settings)
            )
            if error:
                snapshot.status, snapshot.error_code, snapshot.finished_at = (
                    "FAILED",
                    error,
                    utcnow(),
                )
                snapshot.lease_expires_at = None
                continue
            snapshot.status = "RUNNING"
            snapshot.generation = uuid4()
            snapshot.attempts += 1
            snapshot.lease_expires_at = utcnow() + timedelta(seconds=settings.graph_lease_seconds)
            snapshot.error_code = None
            snapshot.finished_at = None
            return Claim(
                snapshot.id, organization_id, document_id, snapshot.version_id, snapshot.generation
            )
    return None


async def owned(db: AsyncSession, claim: Claim) -> tuple[Document, GraphSnapshot] | None:
    document = await db.scalar(
        select(Document)
        .where(Document.id == claim.document_id, Document.organization_id == claim.organization_id)
        .with_for_update()
    )
    snapshot = await db.scalar(
        select(GraphSnapshot)
        .where(
            GraphSnapshot.id == claim.id,
            GraphSnapshot.organization_id == claim.organization_id,
            GraphSnapshot.version_id == claim.version_id,
        )
        .with_for_update()
    )
    if (
        document is None
        or snapshot is None
        or snapshot.status != "RUNNING"
        or snapshot.generation != claim.generation
        or snapshot.lease_expires_at is None
        or as_utc(snapshot.lease_expires_at) <= utcnow()
    ):
        return None
    return document, snapshot


async def run_once(
    factory: async_sessionmaker[AsyncSession], store: GraphStore, settings: Settings
) -> bool:
    claim = await claim_next(factory, settings)
    if claim is None:
        return False
    try:
        async with asyncio.timeout(settings.graph_timeout_seconds):
            async with factory() as db:
                snapshot = await db.scalar(
                    select(GraphSnapshot).where(
                        GraphSnapshot.id == claim.id,
                        GraphSnapshot.organization_id == claim.organization_id,
                        GraphSnapshot.document_id == claim.document_id,
                        GraphSnapshot.version_id == claim.version_id,
                    )
                )
                if snapshot is None:
                    raise GraphFailure("GRAPH_NOT_FOUND")
                assertions = await verify(db, snapshot)
            await store.provision()
            await store.project(claim.scope(), assertions)
            async with factory() as db, db.begin():
                records = await owned(db, claim)
                if records is None:
                    return True
                document, snapshot = records
                error = await policy(db, document, snapshot, settings)
                if error:
                    raise GraphFailure(error)
                await verify(db, snapshot)
                snapshot.status, snapshot.finished_at = "READY", utcnow()
                from app.governance.service import audit

                await audit(db, claim.organization_id, "GRAPH_READY", "graph_snapshot", snapshot.id)
                snapshot.lease_expires_at = None
        logger.info(
            "graph_ready",
            extra={"organization_id": str(claim.organization_id), "job_id": str(claim.id)},
        )
    except Exception as exc:  # noqa: BLE001 - durable supervisor exposes only safe codes
        code = (
            exc.code
            if isinstance(exc, GraphFailure)
            else "GRAPH_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "GRAPH_FAILED"
        )
        async with factory() as db, db.begin():
            records = await owned(db, claim)
            if records:
                _, snapshot = records
                snapshot.status, snapshot.error_code, snapshot.finished_at = (
                    "FAILED",
                    code,
                    utcnow(),
                )
                snapshot.lease_expires_at = None
        logger.warning(
            "graph_failed",
            extra={"organization_id": str(claim.organization_id), "job_id": str(claim.id)},
        )
    return True


async def main(once: bool) -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = GraphStore(settings)
    try:
        while True:
            try:
                await run_once(factory, store, settings)
            except Exception:  # noqa: BLE001 - log no credentials or assertion content
                logger.error("graph_worker_dependency_unavailable")
            if once:
                break
            await asyncio.sleep(settings.processing_poll_seconds)
    finally:
        await store.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
