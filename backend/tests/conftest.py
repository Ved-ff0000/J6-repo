import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.dependencies import auth_rate_limit
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_session
from app.documents.storage import LocalObjectStore
from app.main import create_app


@pytest.fixture
def client(tmp_path: object) -> Iterator[TestClient]:
    settings = Settings(environment="test", jwt_secret=SecretStr("test-key-" * 8))
    app = create_app(settings)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/identity.db")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection: object, _: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

    async def setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as db, db.begin():
            yield db

    async def rate_override() -> None:
        pass

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[auth_rate_limit] = rate_override
    with TestClient(app) as test_client:
        test_client.app.state.test_factory = factory  # type: ignore[union-attr]
        yield test_client
    asyncio.run(engine.dispose())


@pytest.fixture
def processing(client: TestClient, tmp_path: Path) -> LocalObjectStore:
    store = LocalObjectStore(tmp_path / "originals")
    client.app.state.object_store = store  # type: ignore[union-attr]
    client.app.state.settings.processing_enabled = True  # type: ignore[union-attr]
    client.app.state.settings.allow_unscanned_processing = True  # type: ignore[union-attr]
    return store
