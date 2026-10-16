import hashlib
import json
import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool, field_validator
from redis.exceptions import RedisError
from sqlalchemy import select

from app.ai.admission import GATE, REFRESH, RELEASE, limiter_keys
from app.ai.embeddings import create_embedding_provider
from app.ai.interfaces import ProviderError, StructuredGeneration
from app.ai.models import ProviderRun
from app.ai.openai import create_llm_provider
from app.ai.prompts import PROMPT_NAME, PROMPT_VERSION, DiagnosticOutput, diagnostic_request
from app.auth.dependencies import Principal, Session, require_permission
from app.auth.models import AuthSession, Organization, User
from app.auth.security import as_utc, utcnow
from app.core.errors import ErrorEnvelope

logger = logging.getLogger("leaseguard.ai")
router = APIRouter(
    prefix="/api/v1/ai",
    tags=["AI providers"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 429, 503)},
)
Admins = Annotated[Principal, Depends(require_permission("ai:manage"))]


class ProbeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    acknowledge_external_request: StrictBool

    @field_validator("acknowledge_external_request")
    @classmethod
    def acknowledgement(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit acknowledgement is required")
        return value


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_key: UUID
    status: str
    provider: str
    profile: str
    configured_model: str
    returned_model: str | None
    model_version: str | None
    provider_response_id: str | None
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    temperature: float
    max_output_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    output_sha256: str | None
    error_code: str | None
    duration_ms: float | None
    created_at: datetime
    finished_at: datetime | None
    replayed: bool = False


@router.get("/config")
async def configuration(request: Request, principal: Admins) -> dict[str, object]:
    settings = request.app.state.settings
    llm = create_llm_provider(settings)
    embedding = create_embedding_provider(settings)
    return {
        "enabled": settings.llm_enabled,
        "credential_configured": settings.llm_api_key is not None,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "profile": llm.profile,
        "embedding": embedding.label,
        "embedding_profile": embedding.profile,
        "probe_limit": settings.llm_probe_limit,
        "probe_window_seconds": settings.llm_probe_window_seconds,
        "prompt_name": PROMPT_NAME,
        "prompt_version": PROMPT_VERSION,
        "supports_tools": False,
        "purpose": "SYNTHETIC_DIAGNOSTIC_ONLY",
    }


@router.get("/probes", response_model=list[RunOut])
async def history(
    principal: Admins,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProviderRun]:
    return list(
        await db.scalars(
            select(ProviderRun)
            .where(ProviderRun.organization_id == principal.organization_id)
            .order_by(ProviderRun.created_at.desc(), ProviderRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


@router.get("/probes/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID, principal: Admins, db: Session) -> ProviderRun:
    run = await db.scalar(
        select(ProviderRun).where(
            ProviderRun.id == run_id, ProviderRun.organization_id == principal.organization_id
        )
    )
    if run is None:
        raise HTTPException(404, "Provider diagnostic not found")
    return run


@router.post(
    "/probes",
    response_model=RunOut,
    responses={
        202: {
            "model": RunOut,
            "description": "Recorded request is pending or has an unknown outcome; no repeat egress",
        }
    },
)
async def probe(body: ProbeIn, request: Request, response: Response, principal: Admins) -> RunOut:
    settings = request.app.state.settings
    if not settings.llm_enabled:
        raise HTTPException(503, "PROVIDER_DISABLED")
    factory = request.app.state.session_factory
    async with factory() as db:
        existing = await db.scalar(
            select(ProviderRun).where(
                ProviderRun.organization_id == principal.organization_id,
                ProviderRun.request_key == body.request_key,
            )
        )
        if existing:
            response.status_code = 202 if existing.status == "STARTED" else 200
            return RunOut.model_validate(existing).model_copy(update={"replayed": True})
    count_key, lease_key = limiter_keys(settings, principal.organization_id)
    token = uuid4().hex
    redis = request.app.state.redis
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
                settings.llm_timeout_seconds + 60,
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
        provider = create_llm_provider(settings)
        payload = diagnostic_request(min(256, settings.llm_max_output_tokens))
        async with factory() as db, db.begin():
            # A replay that raced acquisition is read, not sent upstream again.
            existing = await db.scalar(
                select(ProviderRun).where(
                    ProviderRun.organization_id == principal.organization_id,
                    ProviderRun.request_key == body.request_key,
                )
            )
            if existing:
                response.status_code = 202 if existing.status == "STARTED" else 200
                return RunOut.model_validate(existing).model_copy(update={"replayed": True})
            run = ProviderRun(
                organization_id=principal.organization_id,
                requested_by=principal.user.id,
                request_key=body.request_key,
                provider=provider.name,
                profile=provider.profile,
                configured_model=provider.model,
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
                prompt_sha256=hashlib.sha256(
                    json.dumps(
                        [message.model_dump() for message in payload.messages],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                temperature=payload.temperature,
                max_output_tokens=payload.max_output_tokens,
            )
            db.add(run)
            await db.flush()
            run_id = run.id
            from app.governance.service import audit

            await audit(
                db,
                principal.organization_id,
                "PROVIDER_STARTED",
                "provider_run",
                run.id,
                principal.user.id,
                request.state.request_id,
            )
        result: StructuredGeneration[DiagnosticOutput] | None = None
        error = None
        try:
            try:
                refreshed = int(
                    await redis.eval(
                        REFRESH, 1, lease_key, token, settings.llm_timeout_seconds + 15
                    )
                )
            except (RedisError, OSError, TimeoutError) as exc:
                raise ProviderError("PROVIDER_LIMITER_UNAVAILABLE") from exc
            if refreshed != 1:
                raise ProviderError("PROVIDER_BUSY")
            result = await provider.generate_structured(payload, DiagnosticOutput)
        except ProviderError as exc:
            error = exc.code
        async with factory() as db, db.begin():
            run = await db.scalar(
                select(ProviderRun)
                .where(
                    ProviderRun.id == run_id,
                    ProviderRun.organization_id == principal.organization_id,
                )
                .with_for_update()
            )
            if run is None:
                raise HTTPException(503, "PROVIDER_RUN_UNAVAILABLE")
            actor = await db.scalar(
                select(User).where(
                    User.id == principal.user.id, User.organization_id == principal.organization_id
                )
            )
            organization = await db.get(Organization, principal.organization_id)
            session = await db.scalar(
                select(AuthSession).where(
                    AuthSession.id == principal.session.id,
                    AuthSession.organization_id == principal.organization_id,
                )
            )
            revoked = (
                actor is None
                or actor.status != "ACTIVE"
                or actor.role not in {"ORG_ADMIN", "SUPER_ADMIN"}
                or organization is None
                or organization.status != "ACTIVE"
                or session is None
                or session.revoked_at is not None
                or as_utc(session.expires_at) <= utcnow()
            )
            if revoked:
                error = "AUTHORIZATION_REVOKED"
            run.status = "FAILED" if error else "SUCCEEDED"
            run.error_code = error
            run.finished_at = utcnow()
            if result is not None:
                run.model_version = result.result.model_version
                run.returned_model = result.result.returned_model
                run.provider_response_id = result.result.response_id
                run.duration_ms = result.result.duration_ms
                run.output_sha256 = hashlib.sha256(result.result.content.encode()).hexdigest()
                if result.result.usage is not None:
                    run.input_tokens = result.result.usage.input_tokens
                    run.output_tokens = result.result.usage.output_tokens
                    run.total_tokens = result.result.usage.total_tokens
            await db.flush()
            from app.governance.service import audit

            await audit(
                db,
                principal.organization_id,
                "PROVIDER_COMPLETED",
                "provider_run",
                run.id,
                principal.user.id,
                request.state.request_id,
                payload={"status": run.status},
            )
            output = RunOut.model_validate(run)
        logger.info(
            "provider_diagnostic_recorded",
            extra={
                "organization_id": str(principal.organization_id),
                "user_id": str(principal.user.id),
                "request_id": getattr(request.state, "request_id", None),
            },
        )
        if error:
            raise HTTPException(403 if error == "AUTHORIZATION_REVOKED" else 503, error)
        return output
    finally:
        try:
            await redis.eval(RELEASE, 1, lease_key, token)
        except (RedisError, OSError, TimeoutError):
            logger.warning("provider_limiter_release_unavailable")
