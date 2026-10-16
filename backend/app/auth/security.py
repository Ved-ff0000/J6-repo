import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException
from pwdlib import PasswordHash
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import Settings

password_hasher = PasswordHash.recommended()
# Equal-cost verification for unknown accounts. Never used as an account credential.
DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(32))


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"}
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    # SQLite tests return naive timestamps; PostgreSQL stores timezone-aware timestamps.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def refresh_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sub: UUID
    org: UUID
    sid: UUID
    exp: int
    iat: int
    jti: UUID
    typ: str


def access_token(user_id: UUID, organization_id: UUID, session_id: UUID, config: Settings) -> str:
    now = utcnow()
    return jwt.encode(
        {
            "sub": str(user_id),
            "org": str(organization_id),
            "sid": str(session_id),
            "iat": now,
            "exp": now + timedelta(seconds=config.access_token_seconds),
            "jti": str(uuid4()),
            "typ": "access",
            "iss": config.jwt_issuer,
            "aud": config.jwt_audience,
        },
        config.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_access(token: str, config: Settings) -> AccessClaims:
    try:
        data = jwt.decode(
            token,
            config.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=config.jwt_issuer,
            audience=config.jwt_audience,
            options={"require": ["sub", "org", "sid", "iat", "exp", "jti", "typ", "iss", "aud"]},
        )
        claims = AccessClaims.model_validate(data)
        if claims.typ != "access":
            raise unauthorized()
        return claims
    except (jwt.InvalidTokenError, ValidationError) as exc:
        raise unauthorized() from exc
