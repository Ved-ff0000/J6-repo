"""Application factory and managed infrastructure lifecycle."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint

from app.ai.router import router as ai_router
from app.auth.router import router as auth_router
from app.conflicts.router import router as conflicts_router
from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.health import router
from app.core.logging import configure_logging
from app.core.observability import Telemetry
from app.db.session import create_engine
from app.documents.limits import UploadLimitMiddleware
from app.documents.router import router as documents_router
from app.documents.storage import create_store
from app.governance.router import router as governance_router
from app.graph.router import router as graph_router
from app.graph.store import GraphStore
from app.operations.live import router as live_router
from app.operations.router import router as operations_router
from app.processing.router import router as processing_router
from app.rules.router import router as rules_router
from app.search.router import router as search_router
from app.search.store import VectorStore
from app.workflows.router import router as workflows_router

logger = logging.getLogger("leaseguard.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    configure_logging(config.log_level)
    telemetry = Telemetry(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        redis: Redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            socket_connect_timeout=config.dependency_timeout_seconds,
            socket_timeout=config.dependency_timeout_seconds,
        )
        app.state.settings = config
        app.state.engine = engine
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state.redis = redis
        app.state.object_store = create_store(config)
        app.state.vector_store = VectorStore(config)
        app.state.graph_store = GraphStore(config)
        try:
            yield
        finally:
            await app.state.graph_store.close()
            await app.state.vector_store.close()
            await redis.aclose()
            await engine.dispose()
            telemetry.close()

    application = FastAPI(title="LeaseGuard AI", version="0.1.0", lifespan=lifespan)
    application.state.telemetry = telemetry
    register_error_handlers(application)
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(documents_router)
    application.include_router(processing_router)
    application.include_router(search_router)
    application.include_router(graph_router)
    application.include_router(ai_router)
    application.include_router(workflows_router)
    application.include_router(rules_router)
    application.include_router(conflicts_router)
    application.include_router(governance_router)
    application.include_router(operations_router)
    application.include_router(live_router)
    application.add_middleware(
        UploadLimitMiddleware, max_body_bytes=config.max_upload_bytes + 64 * 1024
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Generate server-owned IDs; do not trust arbitrary client headers/log content.
        request.state.request_id = str(uuid4())
        started = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            if request.url.path.startswith(
                (
                    "/api/v1",
                    "/api/v1/auth",
                    "/api/v1/users",
                    "/api/v1/organizations",
                    "/api/v1/documents",
                    "/api/v1/search",
                    "/api/v1/graph",
                    "/api/v1/ai",
                )
            ):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            if config.environment == "production":
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            telemetry.observe(request.method, route, status, perf_counter() - started)
            logger.info(
                "request_completed",
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "organization_id": getattr(request.state, "organization_id", None),
                    "user_id": getattr(request.state, "user_id", None),
                    "status_code": status,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
            )

    return application


app = create_app()
