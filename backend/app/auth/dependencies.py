import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuthSession, Organization, Role, User
from app.auth.security import as_utc, decode_access, unauthorized, utcnow
from app.db.session import get_session

Session = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: User
    organization: Organization
    session: AuthSession

    @property
    def organization_id(self) -> UUID:
        """Reusable trusted tenant context for subsequent domain modules."""
        return self.organization.id


async def current_principal(
    request: Request,
    db: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise unauthorized()
    claims = decode_access(credentials.credentials, request.app.state.settings)
    # Every lookup binds identity AND tenant from signed claims; headers/body never select tenant.
    result = await db.execute(
        select(User, Organization, AuthSession)
        .join(Organization, Organization.id == User.organization_id)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(
            User.id == claims.sub,
            User.organization_id == claims.org,
            Organization.id == claims.org,
            AuthSession.id == claims.sid,
            AuthSession.organization_id == claims.org,
        )
    )
    row = result.one_or_none()
    if row is None:
        raise unauthorized()
    user, organization, session = row
    if (
        user.status != "ACTIVE"
        or organization.status != "ACTIVE"
        or session.revoked_at is not None
        or as_utc(session.expires_at) <= utcnow()
    ):
        raise unauthorized()
    request.state.organization_id = str(organization.id)
    request.state.user_id = str(user.id)
    return Principal(user=user, organization=organization, session=session)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]

# Explicit capabilities, default-deny. SUPER_ADMIN is deliberately not a tenant bypass.
PERMISSIONS: dict[str, frozenset[Role]] = {
    "documents:read": frozenset(Role),
    "documents:write": frozenset({Role.SUPER_ADMIN, Role.ORG_ADMIN, Role.ANALYST}),
    "reviews:read": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN, Role.REVIEWER, Role.AUDITOR}),
    "reviews:write": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN, Role.REVIEWER}),
    "audit:read": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN, Role.AUDITOR}),
    "ai:manage": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN}),
    "members:read": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN, Role.AUDITOR}),
    "members:write": frozenset({Role.ORG_ADMIN, Role.SUPER_ADMIN}),
}


def require_permission(permission: str) -> Callable[..., Principal]:
    def check(principal: CurrentPrincipal) -> Principal:
        if principal.user.role not in PERMISSIONS.get(permission, frozenset()):
            raise HTTPException(403, "Permission denied")
        return principal

    return check


RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


async def auth_rate_limit(request: Request) -> None:
    config = request.app.state.settings
    # Trust socket peer only; not spoofable X-Forwarded-For. Proxy deployments must
    # configure trusted proxies deliberately; shared proxy peers share this limit.
    peer = request.client.host if request.client else "unknown"
    key = hmac.new(
        config.jwt_secret.get_secret_value().encode(), peer.encode(), hashlib.sha256
    ).hexdigest()
    try:
        count = await request.app.state.redis.eval(
            RATE_SCRIPT, 1, f"auth:rate:{key}", config.auth_rate_window_seconds
        )
    except (RedisError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "Authentication temporarily unavailable") from exc
    if int(count) > config.auth_rate_limit:
        raise HTTPException(
            429,
            "Too many authentication attempts",
            headers={"Retry-After": str(config.auth_rate_window_seconds)},
        )
