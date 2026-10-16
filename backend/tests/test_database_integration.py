"""Real PostgreSQL/Redis test. Requires migrations and explicit integration opt-in."""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Set LEASEGUARD_RUN_INTEGRATION=1 with migrated PostgreSQL and Redis",
)


def test_live_dependency_readiness() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/ready")
        assert response.status_code == 200, response.text


def test_database_transaction_rollback() -> None:
    import asyncio

    async def scenario() -> None:
        engine = create_engine(Settings())
        factory = async_sessionmaker(engine)
        try:
            with pytest.raises(RuntimeError):
                async with factory() as session, session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO system_metadata (key, value) VALUES ('rollback_probe', 'test')"
                        )
                    )
                    raise RuntimeError("force rollback")
            async with factory() as session:
                result = await session.execute(
                    text("SELECT value FROM system_metadata WHERE key = 'rollback_probe'")
                )
                assert result.scalar_one_or_none() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
