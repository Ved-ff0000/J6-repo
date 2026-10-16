import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.db.models import Base
from app.main import create_app


def test_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEASEGUARD_DATABASE_POOL_SIZE", "8")
    settings = Settings(_env_file=None)
    assert settings.database_pool_size == 8
    assert "leaseguard@" not in repr(settings)
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///test.db", _env_file=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(database_pool_size=0, _env_file=None)


@pytest.mark.parametrize("path", ["/health", "/live", "/api/health", "/api/live"])
def test_liveness_aliases(path: str) -> None:
    with TestClient(create_app()) as client:
        response = client.get(path)
        assert response.status_code == 200
        UUID(response.headers["x-request-id"])


@pytest.mark.parametrize(
    "database,redis,status",
    [(True, True, 200), (False, True, 503), (True, False, 503), (False, False, 503)],
)
def test_readiness(database: bool, redis: bool, status: int) -> None:
    with (
        patch("app.core.health.check_database", new=AsyncMock(return_value=database)),
        patch("app.core.health.check_redis", new=AsyncMock(return_value=redis)),
        patch("app.core.health.check_storage", new=AsyncMock(return_value=True)),
        TestClient(create_app()) as client,
    ):
        response = client.get("/api/ready")
        assert response.status_code == status
        assert response.json()["checks"] == {
            "postgresql": "ok" if database else "unavailable",
            "redis": "ok" if redis else "unavailable",
            "object_storage": "ok",
        }


def test_errors_do_not_leak_input() -> None:
    app = create_app()

    @app.get("/test-validation")
    def validate(count: int) -> int:
        return count

    @app.get("/test-crash")
    def crash() -> None:
        raise RuntimeError("private-secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        for path, status, code in [
            ("/not-found", 404, "HTTP_404"),
            ("/test-validation?count=private-secret", 422, "VALIDATION_ERROR"),
            ("/test-crash", 500, "INTERNAL_ERROR"),
        ]:
            response = client.get(path)
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
            assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
            assert "private-secret" not in response.text


def test_logs_are_structured_and_allowlisted() -> None:
    record = logging.LogRecord("leaseguard", logging.INFO, "", 1, "request_completed", (), None)
    record.request_id = "abc"
    record.authorization = "private-secret"
    output = JsonFormatter().format(record)
    assert json.loads(output)["request_id"] == "abc"
    assert "private-secret" not in output


def test_metadata_is_registered() -> None:
    assert "system_metadata" in Base.metadata.tables


def test_failed_dependencies_are_sanitized() -> None:
    from starlette.requests import Request

    from app.core.health import check_database, check_redis

    app = create_app()
    with TestClient(app):
        request = Request({"type": "http", "app": app})
        with patch.object(
            app.state.redis, "ping", new=AsyncMock(side_effect=RuntimeError("secret"))
        ):
            assert asyncio.run(check_redis(request)) is False
        with patch.object(type(app.state.engine), "connect", side_effect=RuntimeError("secret")):
            assert asyncio.run(check_database(request)) is False
