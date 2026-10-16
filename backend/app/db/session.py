from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"timeout": settings.dependency_timeout_seconds},
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per use; rollback on failures and close on every exit."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with factory() as session, session.begin():
            yield session
    except (OSError, TimeoutError) as exc:
        raise HTTPException(503, "Database temporarily unavailable") from exc
