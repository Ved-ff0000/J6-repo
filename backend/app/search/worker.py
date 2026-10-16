"""Durable index worker. Each lease writes a private generation; SQL publishes it last."""

import argparse
import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from qdrant_client import models
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.dependencies import PERMISSIONS
from app.auth.models import Organization, User
from app.auth.security import as_utc, utcnow
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.documents.models import Document, DocumentMetadata, DocumentStatus, DocumentVersion
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.search.embeddings import Embedder, SearchFailure
from app.search.models import IndexJob
from app.search.store import VectorStore, point_id

logger = logging.getLogger("leaseguard.index")


@dataclass(frozen=True)
class Claim:
    id: UUID
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    generation: UUID


async def authorized(db: AsyncSession, job: IndexJob) -> bool:
    actor = await db.scalar(
        select(User).where(User.id == job.requested_by, User.organization_id == job.organization_id)
    )
    organization = await db.get(Organization, job.organization_id)
    return bool(
        actor is not None
        and actor.status == "ACTIVE"
        and actor.role in PERMISSIONS["documents:write"]
        and organization is not None
        and organization.status == "ACTIVE"
    )


async def claim_next(factory: async_sessionmaker[AsyncSession], settings: Settings) -> Claim | None:
    if not settings.retrieval_enabled:
        return None
    profile = Embedder(settings).profile
    eligible = or_(
        IndexJob.status == "QUEUED",
        and_(IndexJob.status == "RUNNING", IndexJob.lease_expires_at < utcnow()),
    )
    async with factory() as db, db.begin():
        candidates = (
            await db.execute(
                select(IndexJob.id, IndexJob.document_id, IndexJob.organization_id)
                .where(eligible)
                .order_by(IndexJob.created_at, IndexJob.id)
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
            job = await db.scalar(
                select(IndexJob)
                .where(IndexJob.id == identifier, eligible)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                continue
            version = await db.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == job.version_id,
                    DocumentVersion.organization_id == organization_id,
                    DocumentVersion.document_id == document_id,
                )
            )
            error = None
            if job.attempts >= settings.index_max_attempts:
                error = "WORKER_LOST"
            elif job.profile != profile:
                error = "CONFIGURATION_CHANGED"
            elif not await authorized(db, job):
                error = "AUTHORIZATION_REVOKED"
            elif (
                version is None
                or version.processing_status != "PARSED"
                or document.status == DocumentStatus.ARCHIVED
                or version.version_number != document.current_version
            ):
                error = "VERSION_CHANGED"
            elif version.scan_status != "CLEAN" and not settings.allow_unscanned_processing:
                error = "POLICY_BLOCKED"
            if error:
                job.status, job.error_code, job.finished_at = "FAILED", error, utcnow()
                job.lease_expires_at = None
                continue
            job.status = "RUNNING"
            job.generation = uuid4()
            job.attempts += 1
            job.lease_expires_at = utcnow() + timedelta(seconds=settings.index_lease_seconds)
            job.error_code = None
            job.finished_at = None
            job.chunk_count = None
            return Claim(job.id, organization_id, document_id, job.version_id, job.generation)
    return None


async def owned(db: AsyncSession, claim: Claim) -> tuple[Document, IndexJob] | None:
    document = await db.scalar(
        select(Document)
        .where(Document.id == claim.document_id, Document.organization_id == claim.organization_id)
        .with_for_update()
    )
    job = await db.scalar(
        select(IndexJob)
        .where(
            IndexJob.id == claim.id,
            IndexJob.organization_id == claim.organization_id,
            IndexJob.version_id == claim.version_id,
        )
        .with_for_update()
    )
    if (
        document is None
        or job is None
        or job.status != "RUNNING"
        or job.generation != claim.generation
        or job.lease_expires_at is None
        or as_utc(job.lease_expires_at) <= utcnow()
    ):
        return None
    return document, job


def check_source(chunk: DocumentChunk, page: DocumentPage) -> None:
    if (
        chunk.parser_revision != page.parser_revision
        or chunk.start_offset < 0
        or chunk.end_offset > len(page.text)
        or page.text[chunk.start_offset : chunk.end_offset] != chunk.text
        or not chunk.text
    ):
        raise SearchFailure("SOURCE_MISMATCH")


async def run_once(
    factory: async_sessionmaker[AsyncSession], store: VectorStore, settings: Settings
) -> bool:
    claim = await claim_next(factory, settings)
    if claim is None:
        return False
    embedder = Embedder(settings)
    try:
        async with asyncio.timeout(settings.index_timeout_seconds):
            async with factory() as db:
                rows = (
                    await db.execute(
                        select(DocumentChunk, DocumentPage)
                        .join(
                            DocumentPage,
                            and_(
                                DocumentPage.version_id == DocumentChunk.version_id,
                                DocumentPage.organization_id == DocumentChunk.organization_id,
                                DocumentPage.page_number == DocumentChunk.page_number,
                            ),
                        )
                        .where(
                            DocumentChunk.version_id == claim.version_id,
                            DocumentChunk.organization_id == claim.organization_id,
                            DocumentChunk.document_id == claim.document_id,
                        )
                        .order_by(DocumentChunk.chunk_index)
                        .limit(settings.parser_max_chunks + 1)
                    )
                ).all()
                metadata = await db.scalar(
                    select(DocumentMetadata).where(
                        DocumentMetadata.document_id == claim.document_id,
                        DocumentMetadata.organization_id == claim.organization_id,
                    )
                )
                version = await db.get(DocumentVersion, claim.version_id)
                if not rows or len(rows) > settings.parser_max_chunks or version is None:
                    raise SearchFailure("CHUNK_LIMIT")
                parsed = await db.scalar(
                    select(ProcessingJob).where(
                        ProcessingJob.version_id == claim.version_id,
                        ProcessingJob.organization_id == claim.organization_id,
                        ProcessingJob.document_id == claim.document_id,
                        ProcessingJob.status == "SUCCEEDED",
                    )
                )
                if (
                    parsed is None
                    or parsed.chunk_count != len(rows)
                    or any(chunk.parser_revision != parsed.parser_revision for chunk, _ in rows)
                ):
                    raise SearchFailure("SOURCE_MISMATCH")
                version_number = version.version_number
                labels = {
                    "document_type": metadata.document_type if metadata else None,
                    "property": metadata.property_name if metadata else None,
                    "tenant": None,
                    "clause_type": None,
                }
                for chunk, page in rows:
                    check_source(chunk, page)
            await store.provision()
            for start in range(0, len(rows), 32):
                batch = rows[start : start + 32]
                vectors = await embedder.embed([chunk.text for chunk, _ in batch])
                points = []
                for (chunk, _), vector in zip(batch, vectors, strict=True):
                    payload = {
                        "organization_id": str(claim.organization_id),
                        "document_id": str(claim.document_id),
                        "version_id": str(claim.version_id),
                        "document_version": version_number,
                        "generation": str(claim.generation),
                        "chunk_id": str(chunk.id),
                        "page": chunk.page_number,
                        "section": chunk.section,
                        "parser_revision": chunk.parser_revision,
                        "profile": embedder.profile,
                        "source_hash": hashlib.sha256(chunk.text.encode()).hexdigest(),
                        **labels,
                    }
                    points.append(
                        models.PointStruct(
                            id=point_id(claim.generation, chunk.id), vector=vector, payload=payload
                        )
                    )
                await store.upsert(points)
            async with factory() as db, db.begin():
                records = await owned(db, claim)
                if records is None:
                    return True
                document, job = records
                if (
                    document.status == DocumentStatus.ARCHIVED
                    or document.current_version != version_number
                ):
                    raise SearchFailure("VERSION_CHANGED")
                if not await authorized(db, job):
                    raise SearchFailure("AUTHORIZATION_REVOKED")
                job.status, job.chunk_count, job.finished_at = "READY", len(rows), utcnow()
                from app.governance.service import audit

                await audit(db, claim.organization_id, "INDEX_READY", "index_job", job.id)
                job.lease_expires_at = None
        logger.info(
            "index_ready",
            extra={"organization_id": str(claim.organization_id), "job_id": str(claim.id)},
        )
    except Exception as exc:  # noqa: BLE001 - sanitize untrusted dependency failures
        code = (
            exc.code
            if isinstance(exc, SearchFailure)
            else "INDEX_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "INDEX_FAILED"
        )
        async with factory() as db, db.begin():
            records = await owned(db, claim)
            if records is not None:
                _, job = records
                job.status, job.error_code, job.finished_at = "FAILED", code, utcnow()
                job.lease_expires_at = None
        logger.warning(
            "index_failed",
            extra={"organization_id": str(claim.organization_id), "job_id": str(claim.id)},
        )
    return True


async def main(once: bool) -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = VectorStore(settings)
    try:
        while True:
            try:
                await run_once(factory, store, settings)
            except Exception:  # noqa: BLE001 - supervisor logs no credentials
                logger.error("index_worker_dependency_unavailable")
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
