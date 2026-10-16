import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException

from app.documents.storage import StorageUnavailable

logger = logging.getLogger("leaseguard.errors")


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


def error_response(request: Request, status: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(
            request, exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail)
        )
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo input values: they may contain secrets or private document text.
        return error_response(request, 422, "VALIDATION_ERROR", "Request validation failed")

    @app.exception_handler(StorageUnavailable)
    async def storage_unavailable(request: Request, exc: StorageUnavailable) -> JSONResponse:
        return error_response(
            request, 503, "STORAGE_UNAVAILABLE", "Document storage temporarily unavailable"
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError) -> JSONResponse:
        return error_response(request, 409, "CONFLICT", "Request conflicts with existing data")

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            "database_error",
            extra={
                "request_id": getattr(request.state, "request_id", "unknown"),
                "error_type": type(exc).__name__,
            },
        )
        return error_response(
            request, 503, "DEPENDENCY_UNAVAILABLE", "Database temporarily unavailable"
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        extra: dict[str, Any] = {
            "request_id": getattr(request.state, "request_id", "unknown"),
            "error_type": type(exc).__name__,
        }
        logger.error("unhandled_error", extra=extra)
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred")
