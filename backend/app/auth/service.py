import asyncio
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, Organization, RefreshToken, Role, User
from app.auth.schemas import Login, Register, TokenPair
from app.auth.security import (
    DUMMY_HASH,
    access_token,
    as_utc,
    password_hasher,
    refresh_digest,
    unauthorized,
    utcnow,
)
from app.core.config import Settings


async def issue_pair(
    db: AsyncSession, user: User, session: AuthSession, config: Settings
) -> TokenPair:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            session_id=session.id,
            organization_id=session.organization_id,
            token_hash=refresh_digest(raw),
        )
    )
    await db.flush()
    return TokenPair(
        access_token=access_token(user.id, user.organization_id, session.id, config),
        refresh_token=raw,
        expires_in=config.access_token_seconds,
    )


async def start_session(db: AsyncSession, user: User, config: Settings) -> TokenPair:
    session = AuthSession(
        user_id=user.id,
        organization_id=user.organization_id,
        expires_at=utcnow() + timedelta(seconds=config.refresh_token_seconds),
    )
    user.last_login_at = utcnow()
    db.add(session)
    await db.flush()
    from app.governance.service import audit

    await audit(db, user.organization_id, "SESSION_STARTED", "session", session.id, user.id)
    return await issue_pair(db, user, session, config)


async def register(db: AsyncSession, data: Register, config: Settings) -> TokenPair:
    if not config.registration_enabled:
        raise HTTPException(403, "Registration is disabled")
    organization = Organization(name=data.organization_name, slug=data.organization_slug)
    db.add(organization)
    await db.flush()
    user = User(
        organization_id=organization.id,
        email=str(data.email),
        password_hash=await asyncio.to_thread(
            password_hasher.hash, data.password.get_secret_value()
        ),
        first_name=data.first_name,
        last_name=data.last_name,
        role=Role.ORG_ADMIN,
    )
    db.add(user)
    await db.flush()
    from app.governance.service import audit

    await audit(
        db,
        user.organization_id,
        "USER_CREATED",
        "user",
        user.id,
        user.id,
        payload={"role": str(user.role)},
    )
    return await start_session(db, user, config)


async def login(db: AsyncSession, data: Login, config: Settings) -> TokenPair:
    row = (
        await db.execute(
            select(User, Organization).join(Organization).where(User.email == str(data.email))
        )
    ).one_or_none()
    hashed = row[0].password_hash if row is not None else DUMMY_HASH
    valid = await asyncio.to_thread(
        password_hasher.verify, data.password.get_secret_value(), hashed
    )
    if row is None or not valid or row[0].status != "ACTIVE" or row[1].status != "ACTIVE":
        raise unauthorized()
    return await start_session(db, row[0], config)


async def rotate_refresh(db: AsyncSession, raw: str, config: Settings) -> TokenPair:
    digest = refresh_digest(raw)
    session_id = await db.scalar(
        select(RefreshToken.session_id).where(RefreshToken.token_hash == digest)
    )
    if session_id is None:
        raise unauthorized()
    # Lock family BEFORE reading mutable token state. Serializes refresh and logout on PostgreSQL.
    session = await db.scalar(
        select(AuthSession).where(AuthSession.id == session_id).with_for_update()
    )
    if session is None or session.revoked_at is not None or as_utc(session.expires_at) <= utcnow():
        raise unauthorized()
    token = await db.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == digest,
            RefreshToken.session_id == session.id,
            RefreshToken.organization_id == session.organization_id,
        )
    )
    if token is None:
        raise unauthorized()
    if token.used_at is not None:
        session.revoked_at = utcnow()
        # Persist replay revocation BEFORE raising; normal exception rollback must not undo it.
        await db.commit()
        raise unauthorized()
    user = await db.scalar(
        select(User).where(
            User.id == session.user_id, User.organization_id == session.organization_id
        )
    )
    organization = await db.get(Organization, session.organization_id)
    if (
        user is None
        or organization is None
        or user.status != "ACTIVE"
        or organization.status != "ACTIVE"
    ):
        raise unauthorized()
    token.used_at = utcnow()
    return await issue_pair(db, user, session, config)
