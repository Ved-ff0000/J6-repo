"""Durable PostgreSQL queue worker. No API-process background task or Redis enqueue gap."""

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.auth.dependencies import PERMISSIONS
from app.auth.models import Organization, User
from app.auth.security import as_utc, utcnow
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_engine
from app.documents.models import Document, DocumentStatus, DocumentVersion
from app.documents.service import verify_download
from app.documents.storage import ObjectStore, StorageUnavailable, create_store
from app.processing.models import DocumentChunk, DocumentPage, ProcessingJob
from app.processing.parsers import (
    PARSER_REVISION,
    Chunk,
    ParsedDocument,
    ParseFailure,
    ParseLimits,
    chunk_document,
    enforce,
)

logger = logging.getLogger("leaseguard.processing")


@dataclass(frozen=True)
class Claim:
    id: UUID
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    token: UUID


def parse_limits(config: Settings) -> ParseLimits:
    return ParseLimits(
        max_pages=config.parser_max_pages,
        max_characters=config.parser_max_characters,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        max_chunks=config.parser_max_chunks,
    )


async def parse_isolated(
    content: bytes, mime: str, config: Settings
) -> tuple[ParsedDocument, list[Chunk]]:
    with tempfile.TemporaryDirectory(prefix="leaseguard-parse-") as directory:
        root = Path(directory)
        source, output = root / "original", root / "output.json"
        await asyncio.to_thread(source.write_bytes, content)
        # Explicit environment: no database/storage/JWT secrets in parser child processes.
        env = {
            "PATH": os.defpath,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.processing.parse_runner",
            str(source),
            str(output),
            mime,
            parse_limits(config).model_dump_json(),
            cwd=directory,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), config.parser_timeout_seconds)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ParseFailure("PARSER_TIMEOUT") from None
        if (
            process.returncode != 0
            or output.is_symlink()
            or not output.is_file()
            or output.stat().st_size > 32 * 1024 * 1024
        ):
            raise ParseFailure("PARSER_RESOURCE_LIMIT")
        try:
            payload = json.loads(await asyncio.to_thread(output.read_text, encoding="utf-8"))
            if "error" in payload:
                allowed = {
                    "OCR_REQUIRED",
                    "ENCRYPTED_PDF",
                    "PAGE_LIMIT",
                    "TEXT_LIMIT",
                    "NO_TEXT",
                    "INVALID_TEXT",
                    "INVALID_DOCX",
                    "UNSUPPORTED_TYPE",
                    "CHUNK_LIMIT",
                    "PARSE_FAILED",
                }
                raise ParseFailure(
                    payload["error"] if payload["error"] in allowed else "PARSE_FAILED"
                )
            document = ParsedDocument.model_validate(payload["document"])
            limits = parse_limits(config)
            enforce(document, limits)
            if document.parser_revision != PARSER_REVISION or [
                p.number for p in document.pages
            ] != list(range(1, len(document.pages) + 1)):
                raise ParseFailure("INVALID_PARSER_OUTPUT")
            # Derive chunks again from validated pages; don't trust offsets returned by a child.
            return document, chunk_document(document, limits)
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseFailure("INVALID_PARSER_OUTPUT") from exc


def eligible() -> ColumnElement[bool]:
    return or_(
        ProcessingJob.status == "QUEUED",
        and_(ProcessingJob.status == "RUNNING", ProcessingJob.lease_expires_at < utcnow()),
    )


async def claim_next(factory: async_sessionmaker[AsyncSession], config: Settings) -> Claim | None:
    async with factory() as db, db.begin():
        candidates = (
            await db.execute(
                select(ProcessingJob.id, ProcessingJob.document_id, ProcessingJob.organization_id)
                .where(eligible())
                .order_by(ProcessingJob.created_at, ProcessingJob.id)
                .limit(20)
            )
        ).all()
        for identifier, document_id, organization_id in candidates:
            # Global lock order is document then job, shared with API retry/enqueue.
            document = await db.scalar(
                select(Document)
                .where(Document.id == document_id, Document.organization_id == organization_id)
                .with_for_update(skip_locked=True)
            )
            if document is None:
                continue
            job = await db.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.id == identifier, eligible())
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
            user = await db.scalar(
                select(User).where(
                    User.id == job.requested_by, User.organization_id == organization_id
                )
            )
            organization = await db.get(Organization, organization_id)
            error = None
            if job.attempts >= config.processing_max_attempts:
                error = "WORKER_LOST"
            elif (
                user is None
                or organization is None
                or user.status != "ACTIVE"
                or organization.status != "ACTIVE"
                or user.role not in PERMISSIONS["documents:write"]
            ):
                error = "AUTHORIZATION_REVOKED"
            elif (
                version is None
                or version.version_number != document.current_version
                or document.status == DocumentStatus.ARCHIVED
            ):
                error = "VERSION_CHANGED"
            elif version.scan_status != "CLEAN" and not config.allow_unscanned_processing:
                error = "POLICY_BLOCKED"
            if error is not None:
                job.status, job.stage, job.error_code, job.finished_at = (
                    "FAILED",
                    "FAILED",
                    error,
                    utcnow(),
                )
                job.claim_token, job.lease_expires_at = None, None
                if (
                    version is not None
                    and version.version_number == document.current_version
                    and document.status != DocumentStatus.ARCHIVED
                ):
                    document.status = DocumentStatus.FAILED
                from app.governance.service import audit

                await audit(
                    db,
                    job.organization_id,
                    "PROCESSING_FAILED",
                    "document",
                    job.document_id,
                    payload={"error_code": error},
                )
                if version is not None:
                    version.processing_status = "FAILED"
                continue
            token = uuid4()
            job.status, job.stage, job.progress = "RUNNING", "PARSING", 10
            job.attempts += 1
            job.claim_token = token
            job.lease_expires_at = utcnow() + timedelta(seconds=config.processing_lease_seconds)
            job.started_at, job.finished_at, job.error_code = utcnow(), None, None
            document.status = DocumentStatus.PARSING
            assert version is not None
            version.processing_status = "PARSING"
            return Claim(job.id, organization_id, document_id, job.version_id, token)
    return None


async def owned(
    db: AsyncSession, claim: Claim
) -> tuple[Document, ProcessingJob, DocumentVersion] | None:
    document = await db.scalar(
        select(Document)
        .where(Document.id == claim.document_id, Document.organization_id == claim.organization_id)
        .with_for_update()
    )
    job = await db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == claim.id, ProcessingJob.organization_id == claim.organization_id)
        .with_for_update()
    )
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == claim.version_id,
            DocumentVersion.organization_id == claim.organization_id,
            DocumentVersion.document_id == claim.document_id,
        )
    )
    if (
        document is None
        or job is None
        or version is None
        or job.status != "RUNNING"
        or job.claim_token != claim.token
        or job.lease_expires_at is None
        or as_utc(job.lease_expires_at) <= utcnow()
    ):
        return None
    return document, job, version


async def run_once(
    factory: async_sessionmaker[AsyncSession], store: ObjectStore, config: Settings
) -> bool:
    if not config.processing_enabled:
        return False
    claim = await claim_next(factory, config)
    if claim is None:
        return False
    try:
        async with factory() as db:
            version = await db.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == claim.version_id,
                    DocumentVersion.organization_id == claim.organization_id,
                    DocumentVersion.document_id == claim.document_id,
                )
            )
            if version is None:
                raise ParseFailure("VERSION_CHANGED")
            content = await verify_download(store, version)
            mime = version.mime_type
        parsed, chunks = await parse_isolated(content, mime, config)
        async with factory() as db, db.begin():
            records = await owned(db, claim)
            if records is None:
                return True  # Lease was reclaimed; stale workers must never publish.
            document, job, version = records
            actor = await db.scalar(
                select(User).where(
                    User.id == job.requested_by, User.organization_id == claim.organization_id
                )
            )
            organization = await db.get(Organization, claim.organization_id)
            if (
                actor is None
                or organization is None
                or actor.status != "ACTIVE"
                or organization.status != "ACTIVE"
                or actor.role not in PERMISSIONS["documents:write"]
            ):
                raise ParseFailure("AUTHORIZATION_REVOKED")
            for item in parsed.pages:
                db.add(
                    DocumentPage(
                        organization_id=claim.organization_id,
                        document_id=claim.document_id,
                        version_id=claim.version_id,
                        page_number=item.number,
                        page_number_kind=parsed.page_number_kind,
                        text=item.text,
                        headings=[h.model_dump() for h in item.headings],
                        tables=item.tables,
                        parser_revision=PARSER_REVISION,
                    )
                )
            await db.flush()
            for chunk in chunks:
                db.add(
                    DocumentChunk(
                        organization_id=claim.organization_id,
                        document_id=claim.document_id,
                        version_id=claim.version_id,
                        chunk_index=chunk.index,
                        page_number=chunk.page_number,
                        start_offset=chunk.start,
                        end_offset=chunk.end,
                        section=chunk.section,
                        text=chunk.text,
                        parser_revision=PARSER_REVISION,
                    )
                )
            job.status, job.stage, job.progress = "SUCCEEDED", "PARSED", 100
            job.segment_count, job.chunk_count = len(parsed.pages), len(chunks)
            job.character_count = sum(len(p.text) for p in parsed.pages)
            job.page_number_kind, job.finished_at = parsed.page_number_kind, utcnow()
            job.claim_token, job.lease_expires_at = None, None
            version.processing_status = "PARSED"
            version.page_count = (
                len(parsed.pages) if parsed.page_number_kind == "PHYSICAL" else None
            )
            version.parsed_segment_count, version.page_number_kind = (
                len(parsed.pages),
                parsed.page_number_kind,
            )
            if version.version_number == document.current_version:
                document.status = DocumentStatus.PARSED
            from app.governance.service import audit

            await audit(
                db,
                claim.organization_id,
                "DOCUMENT_PROCESSED",
                "document",
                claim.document_id,
                payload={"version_id": str(claim.version_id)},
            )
        logger.info(
            "document_parsed",
            extra={
                "organization_id": str(claim.organization_id),
                "document_id": str(claim.document_id),
                "job_id": str(claim.id),
            },
        )
    except Exception as exc:  # noqa: BLE001 - isolate a failed job; persist only an allowlisted error code
        code = (
            exc.code
            if isinstance(exc, ParseFailure)
            else "STORAGE_UNAVAILABLE"
            if isinstance(exc, StorageUnavailable)
            else "PROCESSING_FAILED"
        )
        async with factory() as db, db.begin():
            records = await owned(db, claim)
            if records is not None:
                document, job, version = records
                job.status, job.stage, job.error_code, job.finished_at = (
                    "FAILED",
                    "FAILED",
                    code,
                    utcnow(),
                )
                job.claim_token, job.lease_expires_at = None, None
                version.processing_status = "FAILED"
                if version.version_number == document.current_version:
                    document.status = DocumentStatus.FAILED
                from app.governance.service import audit

                await audit(
                    db,
                    claim.organization_id,
                    "PROCESSING_FAILED",
                    "document",
                    claim.document_id,
                    payload={"error_code": code},
                )
        logger.warning(
            "document_processing_failed",
            extra={
                "organization_id": str(claim.organization_id),
                "document_id": str(claim.document_id),
                "job_id": str(claim.id),
                "error_type": code,
            },
        )
    return True


async def serve(once: bool = False) -> None:
    config = Settings()
    configure_logging(config.log_level)
    engine = create_engine(config)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = create_store(config)
    try:
        while True:
            try:
                worked = await run_once(factory, store, config)
            except Exception:  # noqa: BLE001 - infrastructure failure; no secrets or parser contents in logs
                logger.error("processing_worker_infrastructure_unavailable")
                if once:
                    raise SystemExit(1) from None
                worked = False
            if once:
                break
            if not worked:
                await asyncio.sleep(config.processing_poll_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(serve("--once" in sys.argv))
