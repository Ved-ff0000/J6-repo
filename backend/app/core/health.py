import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    service: str = "leaseguard-api"
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "unavailable"]]


@router.get("/health", response_model=HealthResponse)
@router.get("/live", response_model=HealthResponse)
@router.get("/api/health", response_model=HealthResponse)
@router.get("/api/live", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only; never implies database or integration readiness."""
    return HealthResponse(status="ok")


async def check_database(request: Request) -> bool:
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            async with request.app.state.engine.connect() as connection:
                # Verify migrations, not just network reachability.
                result = await connection.execute(
                    text("SELECT value FROM system_metadata WHERE key = 'schema_baseline'")
                )
                return bool(result.scalar_one_or_none() == "phase24")
    except Exception:  # noqa: BLE001 - fail closed; never expose service exceptions  # noqa: BLE001 - readiness must fail closed for any dependency failure
        return False


async def check_redis(request: Request) -> bool:
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            return bool(await request.app.state.redis.ping())
    except Exception:  # noqa: BLE001 - fail closed; never expose service exceptions  # noqa: BLE001 - readiness must fail closed for any dependency failure
        return False


async def check_storage(request: Request) -> bool:
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            return bool(await request.app.state.object_store.healthy())
    except Exception:  # noqa: BLE001 - fail closed; never expose service exceptions  # noqa: BLE001 - fail closed without exposing credentials or SDK exceptions
        return False


async def check_vector(request: Request) -> bool:
    if not request.app.state.settings.retrieval_enabled:
        return True
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            return bool(await request.app.state.vector_store.healthy())
    except Exception:  # noqa: BLE001 - fail closed; never expose service exceptions
        return False


async def check_graph(request: Request) -> bool:
    if not request.app.state.settings.graph_enabled:
        return True
    try:
        async with asyncio.timeout(request.app.state.settings.dependency_timeout_seconds):
            return bool(await request.app.state.graph_store.healthy())
    except Exception:  # noqa: BLE001 - readiness must fail closed
        return False


@router.get(
    "/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
@router.get(
    "/api/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
async def ready(request: Request) -> JSONResponse:
    database, redis, storage, vector, graph = await asyncio.gather(
        check_database(request),
        check_redis(request),
        check_storage(request),
        check_vector(request),
        check_graph(request),
    )
    result = ReadinessResponse(
        status="ready" if database and redis and storage and vector and graph else "not_ready",
        checks={
            "postgresql": "ok" if database else "unavailable",
            "redis": "ok" if redis else "unavailable",
            "object_storage": "ok" if storage else "unavailable",
            **(
                {"neo4j": "ok" if graph else "unavailable"}
                if request.app.state.settings.graph_enabled
                else {}
            ),
            **(
                {"qdrant": "ok" if vector else "unavailable"}
                if request.app.state.settings.retrieval_enabled
                else {}
            ),
        },
    )
    return JSONResponse(
        result.model_dump(),
        status_code=200 if database and redis and storage and vector and graph else 503,
    )
