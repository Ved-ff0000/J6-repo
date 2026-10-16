from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.ai.openai import strict_schema
from app.auth.dependencies import Principal, Session, require_permission
from app.core.errors import ErrorEnvelope
from app.documents.models import DocumentVersion
from app.extraction.schemas import GROUPS, SCHEMA_VERSION, LeaseExtraction, LeaseExtractionResult
from app.workflows.agent import LEASE_REVISION, LEASE_STAGES, REVISION, STAGES
from app.workflows.errors import WorkflowFailure
from app.workflows.models import WorkflowRun
from app.workflows.schemas import WorkflowIn, WorkflowOut
from app.workflows.service import run_workflow, visible_version

router = APIRouter(
    prefix="/api/v1",
    tags=["bounded workflows"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 429, 503)},
)
Admins = Annotated[Principal, Depends(require_permission("ai:manage"))]


@router.get("/workflows/config")
async def configuration(request: Request, principal: Admins) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "enabled": settings.workflow_enabled
        and settings.llm_enabled
        and settings.retrieval_enabled,
        "lease_extraction_enabled": settings.lease_extraction_enabled
        and settings.workflow_enabled
        and settings.llm_enabled
        and settings.retrieval_enabled,
        "rules_enabled": settings.rules_enabled,
        "lease_schema_version": SCHEMA_VERSION,
        "lease_revision": LEASE_REVISION,
        "lease_stages": LEASE_STAGES,
        "extraction_modes": ["CANDIDATES", "LEASE_FIELDS"],
        "revision": REVISION,
        "stages": STAGES,
        "scope": "TITLE_AND_HEADING_CANDIDATES_ONLY",
        "max_generation_calls": 2,
        "reserved_admission_units": 2,
        "max_context_chunks": 3,
        "timeout_seconds": settings.workflow_timeout_seconds,
        "automatic_retries": False,
        "automatic_approval": False,
        "review_queue_created": False,
    }


@router.post(
    "/documents/{document_id}/versions/{version_number}/workflow-runs",
    response_model=WorkflowOut,
    responses={
        202: {
            "model": WorkflowOut,
            "description": "Started or unknown prior run; never automatically resumed",
        }
    },
)
async def start(
    document_id: UUID,
    version_number: int,
    body: WorkflowIn,
    request: Request,
    response: Response,
    principal: Admins,
) -> WorkflowOut:
    try:
        result = await run_workflow(
            body,
            document_id,
            version_number,
            principal,
            request.app.state.session_factory,
            request.app.state.settings,
            request.app.state.vector_store,
            request.app.state.redis,
        )
    except WorkflowFailure as exc:
        raise HTTPException(
            409 if exc.code != "WORKFLOW_AUTHORIZATION_REVOKED" else 403, exc.code
        ) from exc
    if result.status == "STARTED":
        response.status_code = 202
    return result


@router.get(
    "/documents/{document_id}/versions/{version_number}/workflow-runs",
    response_model=list[WorkflowOut],
)
async def history(
    document_id: UUID,
    version_number: int,
    principal: Admins,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    extraction_mode: Literal["CANDIDATES", "LEASE_FIELDS"] | None = None,
) -> list[WorkflowOut]:
    version = await visible_version(db, principal.organization_id, document_id, version_number)
    records = await db.scalars(
        select(WorkflowRun)
        .where(
            WorkflowRun.organization_id == principal.organization_id,
            WorkflowRun.document_id == document_id,
            WorkflowRun.version_id == version.id,
            *([WorkflowRun.extraction_mode == extraction_mode] if extraction_mode else []),
        )
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [WorkflowOut.model_validate(item) for item in records]


@router.get("/workflow-runs/{run_id}", response_model=WorkflowOut)
async def get_run(run_id: UUID, principal: Admins, db: Session) -> WorkflowOut:
    run = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id, WorkflowRun.organization_id == principal.organization_id
        )
    )
    if run is None:
        raise HTTPException(404, "Workflow run not found")
    version = await db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == run.version_id,
            DocumentVersion.document_id == run.document_id,
            DocumentVersion.organization_id == principal.organization_id,
        )
    )
    if version is None:
        raise HTTPException(404, "Workflow run not found")
    await visible_version(db, principal.organization_id, run.document_id, version.version_number)
    return WorkflowOut.model_validate(run)


@router.get("/lease-extractions/schema")
async def extraction_schema(principal: Admins) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "groups": GROUPS,
        "max_fields": 24,
        "max_fields_per_group": 6,
        "proposal_schema": strict_schema(LeaseExtraction),
        "result_schema": LeaseExtractionResult.model_json_schema(),
        "all_results_require_review": True,
        "absence_from_document_established": False,
    }
