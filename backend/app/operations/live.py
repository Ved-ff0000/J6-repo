"""SQL-backed invalidations: single-use tickets, no tokens in URLs, fresh session checks."""

import asyncio
import hashlib
import time
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, Organization, User
from app.auth.security import utcnow
from app.governance.models import AuditEvent
from app.operations.models import LiveTicket

router = APIRouter()


class Hello(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=32, max_length=128)
    after: int = Field(default=0, ge=0)


async def authorized(db: AsyncSession, org: UUID, user: UUID, session: UUID) -> bool:
    return bool(
        await db.scalar(
            select(User.id)
            .join(Organization, Organization.id == User.organization_id)
            .join(AuthSession, AuthSession.user_id == User.id)
            .where(
                User.id == user,
                User.organization_id == org,
                User.status == "ACTIVE",
                Organization.status == "ACTIVE",
                AuthSession.id == session,
                AuthSession.organization_id == org,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > utcnow(),
            )
        )
    )


@router.websocket("/api/v1/events/live")
async def live(socket: WebSocket) -> None:
    if not socket.app.state.settings.live_enabled:
        await socket.close(code=1013)
        return
    # Explicit credentials are required even for same-origin connections; never ambient cookies.
    origin = socket.headers.get("origin")
    if origin:
        from urllib.parse import urlsplit

        if urlsplit(origin).netloc != socket.headers.get("host"):
            await socket.close(code=1008)
            return
    connections = getattr(socket.app.state, "live_connections", 0)
    if connections >= 200:
        await socket.close(code=1013)
        return
    socket.app.state.live_connections = connections + 1
    await socket.accept()
    try:
        hello = Hello.model_validate(await asyncio.wait_for(socket.receive_json(), timeout=5))
        async with socket.app.state.session_factory() as db, db.begin():
            ticket = await db.scalar(
                select(LiveTicket)
                .where(
                    LiveTicket.token_sha256 == hashlib.sha256(hello.ticket.encode()).hexdigest(),
                    LiveTicket.used_at.is_(None),
                    LiveTicket.expires_at > utcnow(),
                )
                .with_for_update()
            )
            if ticket is None:
                await socket.close(code=4401)
                return
            org, user, session = ticket.organization_id, ticket.user_id, ticket.session_id
            # Conditional update also makes consumption single-use on SQLite.
            used = await db.execute(
                update(LiveTicket)
                .where(LiveTicket.id == ticket.id, LiveTicket.used_at.is_(None))
                .values(used_at=utcnow())
            )
            if used.rowcount != 1 or not await authorized(db, org, user, session):
                await socket.close(code=4401)
                return
        after = hello.after
        started = time.monotonic()
        while time.monotonic() - started < 900:
            async with socket.app.state.session_factory() as db:
                if not await authorized(db, org, user, session):
                    await socket.close(code=4401)
                    return
                rows = list(
                    await db.scalars(
                        select(AuditEvent)
                        .where(AuditEvent.organization_id == org, AuditEvent.sequence > after)
                        .order_by(AuditEvent.sequence)
                        .limit(100)
                    )
                )
            after = rows[-1].sequence if rows else after
            # Only invalidate keys, not private audit payloads, reasons or document text.
            await socket.send_json(
                {"type": "invalidate" if rows else "heartbeat", "sequence": after}
            )
            await asyncio.sleep(2)
        await socket.close(code=1000)
    except WebSocketDisconnect:
        pass
    except (ValueError, TimeoutError):
        await socket.close(code=1008)
    except Exception:  # noqa: BLE001 - no credentials or exception text sent to client
        try:
            await socket.close(code=1013)
        except RuntimeError:
            pass
    finally:
        socket.app.state.live_connections -= 1
