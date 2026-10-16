import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select, update

from app.auth import service
from app.auth.dependencies import (
    CurrentPrincipal,
    Principal,
    Session,
    auth_rate_limit,
    require_permission,
)
from app.auth.models import AuthSession, Organization, Role, User
from app.auth.schemas import (
    Login,
    MemberCreate,
    MemberUpdate,
    OrganizationOut,
    Refresh,
    Register,
    TokenPair,
    UserOut,
)
from app.auth.security import password_hasher, utcnow
from app.core.errors import ErrorEnvelope

router = APIRouter(
    prefix="/api/v1",
    tags=["identity"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422, 429, 503)},
)
Limited = [Depends(auth_rate_limit)]
Readers = Annotated[Principal, Depends(require_permission("members:read"))]
Writers = Annotated[Principal, Depends(require_permission("members:write"))]


@router.post("/auth/register", response_model=TokenPair, status_code=201, dependencies=Limited)
async def register(data: Register, request: Request, db: Session) -> TokenPair:
    """Atomically create a new organization and its first admin. Cannot join an existing tenant."""
    return await service.register(db, data, request.app.state.settings)


@router.post("/auth/login", response_model=TokenPair, dependencies=Limited)
async def login(data: Login, request: Request, db: Session) -> TokenPair:
    return await service.login(db, data, request.app.state.settings)


@router.post("/auth/refresh", response_model=TokenPair, dependencies=Limited)
async def refresh(data: Refresh, request: Request, db: Session) -> TokenPair:
    return await service.rotate_refresh(
        db, data.refresh_token.get_secret_value(), request.app.state.settings
    )


@router.post("/auth/logout", status_code=204)
async def logout(principal: CurrentPrincipal, db: Session) -> Response:
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == principal.session.id,
            AuthSession.organization_id == principal.organization.id,
        )
        .values(revoked_at=utcnow())
    )
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "SESSION_REVOKED",
        "session",
        principal.session.id,
        principal.user.id,
    )
    await db.commit()
    return Response(status_code=204)


@router.get("/auth/me", response_model=UserOut)
async def me(principal: CurrentPrincipal) -> User:
    return principal.user


@router.get("/organizations/current", response_model=OrganizationOut)
async def organization(principal: CurrentPrincipal) -> OrganizationOut:
    return OrganizationOut.model_validate(principal.organization)


@router.get("/organizations/{organization_id}", response_model=OrganizationOut)
async def organization_by_id(organization_id: UUID, principal: CurrentPrincipal) -> OrganizationOut:
    if organization_id != principal.organization.id:
        raise HTTPException(404, "Organization not found")
    return OrganizationOut.model_validate(principal.organization)


@router.get("/users", response_model=list[UserOut])
async def users(
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[User]:
    return list(
        (
            await db.scalars(
                select(User)
                .where(
                    User.organization_id == principal.organization.id,
                )
                .order_by(User.created_at, User.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(data: MemberCreate, principal: Writers, db: Session) -> User:
    if data.role == Role.SUPER_ADMIN:
        raise HTTPException(403, "Platform role cannot be assigned through tenant APIs")
    user = User(
        organization_id=principal.organization.id,
        email=str(data.email),
        password_hash=await asyncio.to_thread(
            password_hasher.hash, data.password.get_secret_value()
        ),
        first_name=data.first_name,
        last_name=data.last_name,
        role=data.role,
    )
    db.add(user)
    await db.flush()
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "USER_CREATED",
        "user",
        user.id,
        principal.user.id,
        payload={"role": str(user.role), "status": user.status},
    )
    return user


@router.get("/users/{user_id}", response_model=UserOut)
async def user_by_id(user_id: UUID, principal: Readers, db: Session) -> User:
    user = await db.scalar(
        select(User).where(User.id == user_id, User.organization_id == principal.organization.id)
    )
    if user is None:
        raise HTTPException(404, "User not found")
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "USER_VIEWED",
        "user",
        user.id,
        principal.user.id,
        payload={"role": str(user.role), "status": user.status},
    )
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, data: MemberUpdate, principal: Writers, db: Session) -> User:
    # Serialize membership writes per organization. Re-read the actor after the lock,
    # so two admins cannot concurrently disable each other and orphan the tenant.
    await db.scalar(
        select(Organization).where(Organization.id == principal.organization.id).with_for_update()
    )
    await db.refresh(principal.user)
    if principal.user.status != "ACTIVE" or principal.user.role not in {
        Role.ORG_ADMIN,
        Role.SUPER_ADMIN,
    }:
        raise HTTPException(403, "Permission denied")
    user = await db.scalar(
        select(User)
        .where(User.id == user_id, User.organization_id == principal.organization.id)
        .with_for_update()
    )
    if user is None:
        raise HTTPException(404, "User not found")
    if (
        user.id == principal.user.id
        or user.role == Role.SUPER_ADMIN
        or data.role == Role.SUPER_ADMIN
    ):
        raise HTTPException(403, "Cannot change own access or platform roles")
    before = {"role": str(user.role), "status": user.status}
    if data.role is not None:
        user.role = data.role
    if data.status is not None:
        user.status = data.status
    if data.role is not None or data.status is not None:
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.organization_id == principal.organization.id,
            )
            .values(revoked_at=utcnow())
        )
    await db.flush()
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "USER_ACCESS_UPDATED",
        "user",
        user.id,
        principal.user.id,
        payload={"old": before, "new": {"role": str(user.role), "status": user.status}},
    )
    return user
