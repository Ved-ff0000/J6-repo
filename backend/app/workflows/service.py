import asyncio
import logging
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.admission import GATE, REFRESH, RELEASE, limiter_keys
from app.ai.interfaces import ProviderError, StructuredGeneration, T
from app.ai.openai import create_llm_provider
from app.auth.dependencies import Principal
from app.auth.models import AuthSession, Organization, User
from app.auth.security import as_utc, utcnow
from app.core.config import Settings
from app.documents.models import Document, DocumentStatus, DocumentVersion
from app.extraction.schemas import LeaseExtraction
from app.processing.models import DocumentChunk, ProcessingJob
from app.processing.router import scoped_version
from app.rules.catalog import policy as rule_policy
from app.search.embeddings import Embedder
from app.search.models import IndexJob
from app.search.router import Citation, SearchIn, citation, retrieve
from app.search.store import VectorStore
from app.workflows.agent import LEASE_REVISION, REVISION, RULE_LEASE_REVISION, AgentState, execute
from app.workflows.errors import WorkflowFailure
from app.workflows.models import WorkflowRun
from app.workflows.prompts import PROMPT_VERSION, bounded_context, prompt
from app.workflows.schemas import (
    CallMetadata,
    Classification,
    Extraction,
    StageEvent,
    WorkflowIn,
    WorkflowOut,
)

logger = logging.getLogger("leaseguard.workflows")


class AdmissionRedis(Protocol):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> int: ...


def policy(settings: Settings, extraction_mode: str = "CANDIDATES") -> None:
    if extraction_mode == "LEASE_FIELDS" and (
        not settings.lease_extraction_enabled or settings.llm_max_output_tokens < 1024
    ):
        raise HTTPException(503, "LEASE_EXTRACTION_DISABLED")
    if not settings.workflow_enabled or not settings.llm_enabled or not settings.retrieval_enabled:
        raise HTTPException(503, "WORKFLOW_DISABLED")


async def visible_version(db: AsyncSession, org: UUID, doc: UUID, number: int) -> DocumentVersion:
    version = await scoped_version(db, org, doc, number)
    document = await db.get(Document, doc)
    if document is None or document.status == DocumentStatus.ARCHIVED:
        raise HTTPException(404, "Workflow source not found")
    return version


class Context:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        store: VectorStore,
        redis: AdmissionRedis,
        principal: Principal,
        document_id: UUID,
        version_number: int,
        lease_key: str,
        token: str,
        extraction_mode: str = "CANDIDATES",
    ):
        self.factory = factory
        self.settings = settings
        self.store = store
        self.redis = redis
        self.principal = principal
        self.document_id = document_id
        self.version_number = version_number
        self.lease_key = lease_key
        self.token = token
        self.extraction_mode = extraction_mode
        self.provider = create_llm_provider(settings)
        self.run_id: UUID | None = None
        self.version_id: UUID | None = None
        self.checksum: str | None = None

    async def check(self, db: AsyncSession, *, lock: bool = False) -> DocumentVersion:
        policy(self.settings, self.extraction_mode)
        org_id = self.principal.organization_id
        actor = await db.scalar(
            select(User).where(User.id == self.principal.user.id, User.organization_id == org_id)
        )
        org = await db.get(Organization, org_id)
        session = await db.scalar(
            select(AuthSession).where(
                AuthSession.id == self.principal.session.id,
                AuthSession.organization_id == org_id,
                AuthSession.user_id == self.principal.user.id,
            )
        )
        if (
            actor is None
            or actor.status != "ACTIVE"
            or actor.role not in {"ORG_ADMIN", "SUPER_ADMIN"}
            or org is None
            or org.status != "ACTIVE"
            or session is None
            or session.revoked_at is not None
            or as_utc(session.expires_at) <= utcnow()
        ):
            raise WorkflowFailure("WORKFLOW_AUTHORIZATION_REVOKED")
        query = select(Document).where(
            Document.id == self.document_id, Document.organization_id == org_id
        )
        document = await db.scalar(query.with_for_update() if lock else query)
        if document is None:
            raise HTTPException(404, "Document not found")
        version = await scoped_version(db, org_id, self.document_id, self.version_number)
        if (
            document.status == DocumentStatus.ARCHIVED
            or document.current_version != self.version_number
            or version.processing_status != "PARSED"
            or (self.version_id is not None and version.id != self.version_id)
            or (self.checksum is not None and version.checksum != self.checksum)
        ):
            raise WorkflowFailure("WORKFLOW_SOURCE_CHANGED")
        if version.scan_status != "CLEAN" and not self.settings.allow_unscanned_processing:
            raise WorkflowFailure("WORKFLOW_SCAN_REQUIRED")
        job = await db.scalar(
            select(ProcessingJob).where(
                ProcessingJob.organization_id == org_id,
                ProcessingJob.document_id == self.document_id,
                ProcessingJob.version_id == version.id,
                ProcessingJob.status == "SUCCEEDED",
            )
        )
        index = await db.scalar(
            select(IndexJob).where(
                IndexJob.organization_id == org_id,
                IndexJob.document_id == self.document_id,
                IndexJob.version_id == version.id,
                IndexJob.status == "READY",
                IndexJob.profile == Embedder(self.settings).profile,
                IndexJob.generation.is_not(None),
            )
        )
        if job is None or index is None:
            raise WorkflowFailure("WORKFLOW_INDEX_REQUIRED")
        return version

    async def guard(self, sources: list[Citation]) -> None:
        async with self.factory() as db:
            await self.check(db)
            for source in sources:
                if source.document_id != self.document_id or source.version_id != self.version_id:
                    raise WorkflowFailure("WORKFLOW_EVIDENCE_INVALID")
                fresh = await citation(db, self.principal.organization_id, source.chunk_id)
                if fresh.model_dump() != source.model_dump():
                    raise WorkflowFailure("WORKFLOW_SOURCE_CHANGED")

    async def load(self) -> tuple[list[Citation], int]:
        async with self.factory() as db:
            version = await self.check(db)
            query = select(DocumentChunk.id).where(
                DocumentChunk.organization_id == self.principal.organization_id,
                DocumentChunk.document_id == self.document_id,
                DocumentChunk.version_id == version.id,
            )
            total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0
            if total < 1 or total > self.settings.parser_max_chunks:
                raise WorkflowFailure("WORKFLOW_SOURCE_LIMIT")
            ids = await db.scalars(query.order_by(DocumentChunk.chunk_index).limit(3))
            sources = [await citation(db, self.principal.organization_id, key) for key in ids]
            return bounded_context(sources), total

    async def model_call(
        self, kind: str, sources: list[Citation], model: type[T]
    ) -> tuple[T, CallMetadata]:
        await self.guard(sources)
        try:
            owner = int(
                await self.redis.eval(
                    REFRESH,
                    1,
                    self.lease_key,
                    self.token,
                    self.settings.workflow_timeout_seconds + 30,
                )
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise WorkflowFailure("PROVIDER_LIMITER_UNAVAILABLE") from exc
        if owner != 1:
            raise WorkflowFailure("PROVIDER_BUSY")
        if kind not in {"classification", "extraction", "lease_extraction"}:
            raise WorkflowFailure("WORKFLOW_INTERNAL_FAILURE")
        payload, fingerprint = prompt(
            "classification"
            if kind == "classification"
            else "extraction"
            if kind == "extraction"
            else "lease_extraction",
            sources,
            self.settings.llm_max_output_tokens,
        )
        generated: StructuredGeneration[T] = await self.provider.generate_structured(payload, model)
        result = generated.result
        metadata = CallMetadata(
            provider=result.provider,
            profile=self.provider.profile,
            configured_model=result.configured_model,
            returned_model=result.returned_model,
            model_version=result.model_version,
            response_id=result.response_id,
            prompt_name="workflow_" + kind,
            prompt_version=PROMPT_VERSION,
            prompt_sha256=fingerprint,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
            usage=result.usage,
            duration_ms=result.duration_ms,
            recorded_at=utcnow(),
        )
        return generated.data, metadata

    async def classify(self, sources: list[Citation]) -> tuple[Classification, CallMetadata]:
        return await self.model_call("classification", sources, Classification)

    async def retrieve(self, kind: str) -> list[Citation]:
        await self.guard([])
        async with self.factory() as db:
            found = await retrieve(
                SearchIn(
                    query="landlord tenant commencement expiration rent renewal termination notice obligations insurance maintenance assignment CAM escalation"
                    if self.extraction_mode == "LEASE_FIELDS"
                    else "lease amendment document title clause heading",
                    document_id=self.document_id,
                    version_number=self.version_number,
                    top_k=3,
                ),
                self.settings,
                self.store,
                self.principal.organization_id,
                db,
            )
        return bounded_context([item.citation for item in found.hits]) if found.hits else []

    async def extract(self, sources: list[Citation]) -> tuple[Extraction, CallMetadata]:
        return await self.model_call("extraction", sources, Extraction)

    async def extract_lease(self, sources: list[Citation]) -> tuple[LeaseExtraction, CallMetadata]:
        return await self.model_call("lease_extraction", sources, LeaseExtraction)

    async def record(self, stage: str, status: str, calls: list[CallMetadata]) -> None:
        async with self.factory() as db, db.begin():
            run = await db.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == self.run_id,
                    WorkflowRun.organization_id == self.principal.organization_id,
                )
                .with_for_update()
            )
            if run is None or run.status != "STARTED":
                raise WorkflowFailure("WORKFLOW_RUN_UNAVAILABLE")
            event = StageEvent.model_validate(
                {"name": stage, "status": status, "timestamp": utcnow()}
            )
            run.stage = stage
            run.trace = [*run.trace, event.model_dump(mode="json")]
            run.calls = [item.model_dump(mode="json") for item in calls]


async def run_workflow(
    body: WorkflowIn,
    document_id: UUID,
    version_number: int,
    principal: Principal,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    store: VectorStore,
    redis: AdmissionRedis,
) -> WorkflowOut:
    # No caller-provided tenant, source text, model, tools or graph transition is accepted.
    async with factory() as db:
        version = await visible_version(db, principal.organization_id, document_id, version_number)
        existing = await db.scalar(
            select(WorkflowRun).where(
                WorkflowRun.organization_id == principal.organization_id,
                WorkflowRun.request_key == body.request_key,
            )
        )
        if existing:
            if (
                existing.version_id != version.id
                or existing.extraction_mode != body.extraction_mode
            ):
                raise HTTPException(409, "WORKFLOW_REQUEST_KEY_CONFLICT")
            return WorkflowOut.model_validate(existing).model_copy(update={"replayed": True})
    policy(settings, body.extraction_mode)
    count_key, lease_key = limiter_keys(settings, principal.organization_id)
    token = uuid4().hex
    context = Context(
        factory,
        settings,
        store,
        redis,
        principal,
        document_id,
        version_number,
        lease_key,
        token,
        body.extraction_mode,
    )
    async with factory() as db:
        version = await context.check(db)
    context.version_id = version.id
    context.checksum = version.checksum
    try:
        admitted = int(
            await redis.eval(
                GATE,
                2,
                count_key,
                lease_key,
                settings.llm_probe_limit,
                settings.llm_probe_window_seconds,
                token,
                settings.workflow_timeout_seconds + 30,
                2,
            )
        )
    except (RedisError, OSError, TimeoutError) as exc:
        raise HTTPException(503, "PROVIDER_LIMITER_UNAVAILABLE") from exc
    if admitted == -1:
        raise HTTPException(409, "PROVIDER_BUSY")
    if admitted != 1:
        raise HTTPException(
            429,
            "PROVIDER_PROBE_LIMIT",
            headers={"Retry-After": str(settings.llm_probe_window_seconds)},
        )
    try:
        async with factory() as db, db.begin():
            await context.check(db, lock=True)
            existing = await db.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.organization_id == principal.organization_id,
                    WorkflowRun.request_key == body.request_key,
                )
            )
            if existing:
                if (
                    existing.version_id != version.id
                    or existing.extraction_mode != body.extraction_mode
                ):
                    raise HTTPException(409, "WORKFLOW_REQUEST_KEY_CONFLICT")
                return WorkflowOut.model_validate(existing).model_copy(update={"replayed": True})
            created = WorkflowRun(
                organization_id=principal.organization_id,
                requested_by=principal.user.id,
                auth_session_id=principal.session.id,
                document_id=document_id,
                version_id=version.id,
                request_key=body.request_key,
                extraction_mode=body.extraction_mode,
                revision=(RULE_LEASE_REVISION if settings.rules_enabled else LEASE_REVISION)
                if body.extraction_mode == "LEASE_FIELDS"
                else REVISION,
                source_checksum=version.checksum,
            )
            db.add(created)
            await db.flush()
            context.run_id = created.id
            from app.governance.service import audit

            await audit(
                db,
                principal.organization_id,
                "AGENT_STARTED",
                "workflow",
                created.id,
                principal.user.id,
            )
        output = None
        error = None
        try:
            async with asyncio.timeout(settings.workflow_timeout_seconds):
                output = await execute(
                    AgentState(
                        rule_policy=rule_policy(True, settings.rules_disabled)
                        if settings.rules_enabled and body.extraction_mode == "LEASE_FIELDS"
                        else None,
                        extraction_mode=body.extraction_mode,
                        organization_id=principal.organization_id,
                        document_id=document_id,
                        version_id=version.id,
                    ),
                    context,
                )
        except (WorkflowFailure, ProviderError) as exc:
            error = exc.code
        except HTTPException:
            error = "WORKFLOW_SOURCE_UNAVAILABLE"
        except TimeoutError:
            error = "WORKFLOW_TIMEOUT"
        except Exception as exc:  # noqa: BLE001 - persist a fixed safe failure, never exception content
            logger.error("workflow_failed", extra={"error_type": type(exc).__name__})
            error = "WORKFLOW_INTERNAL_FAILURE"
        async with factory() as db, db.begin():
            run = await db.scalar(
                select(WorkflowRun)
                .where(
                    WorkflowRun.id == context.run_id,
                    WorkflowRun.organization_id == principal.organization_id,
                )
                .with_for_update()
            )
            if run is None:
                raise HTTPException(503, "WORKFLOW_RUN_UNAVAILABLE")
            if not error:
                try:
                    await context.check(db, lock=True)
                except WorkflowFailure as exc:
                    error = exc.code
                except HTTPException:
                    error = "WORKFLOW_SOURCE_UNAVAILABLE"
            run.status = "FAILED" if error else "REVIEW_REQUIRED"
            run.error_code = error
            run.finished_at = utcnow()
            run.result = (
                output.result.model_dump(mode="json")
                if not error and output and output.result
                else None
            )
            if not error and run.result is None:
                run.status = "FAILED"
                run.error_code = "WORKFLOW_INTERNAL_FAILURE"
            await db.flush()
            from app.governance.service import audit, enqueue_reviews

            if settings.review_auto_enqueue:
                tasks = await enqueue_reviews(db, run)
                if run.result is not None:
                    run.result = {**run.result, "review_queue_created": bool(tasks)}
            await audit(
                db,
                principal.organization_id,
                "AGENT_FAILED" if run.status == "FAILED" else "AGENT_COMPLETED",
                "workflow",
                run.id,
                principal.user.id,
                payload={"status": run.status},
            )
            result = WorkflowOut.model_validate(run)
        if result.error_code == "WORKFLOW_AUTHORIZATION_REVOKED":
            raise HTTPException(403, "WORKFLOW_AUTHORIZATION_REVOKED")
        return result
    finally:
        try:
            await redis.eval(RELEASE, 1, lease_key, token)
        except (RedisError, OSError, TimeoutError):
            logger.warning("workflow_limiter_release_unavailable")
