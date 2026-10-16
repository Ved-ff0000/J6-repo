"""HTTP identity tests against a real in-process SQL database (not fake auth results).

SQLite is test-only and cannot validate PostgreSQL row locking. Dedicated PostgreSQL
integration cases live in test_auth_integration.py and run in CI.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.auth.dependencies import PERMISSIONS
from app.auth.models import AuthSession, RefreshToken, Role, User
from app.auth.security import access_token, decode_access, password_hasher, refresh_digest, utcnow
from app.core.config import Settings
from app.main import create_app

PASSWORD = "Correct-horse-lease-42"


def register(client: TestClient, slug: str = "alpha", **overrides: object) -> dict[str, str]:
    data = {
        "email": f"admin@{slug}.example.com",
        "password": PASSWORD,
        "first_name": "Ada",
        "last_name": "Admin",
        "organization_name": slug.title(),
        "organization_slug": slug,
    }
    data.update(overrides)
    response = client.post("/api/v1/auth/register", json=data)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def headers(pair: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {pair['access_token']}"}


def create_member(
    client: TestClient,
    pair: dict[str, str],
    role: str = "VIEWER",
    email: str = "member@example.com",
) -> dict[str, str]:
    response = client.post(
        "/api/v1/users",
        headers=headers(pair),
        json={
            "email": email,
            "password": PASSWORD,
            "first_name": "Member",
            "last_name": "User",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_registration_login_and_private_responses(client: TestClient) -> None:
    pair = register(client, email="ADMIN@alpha.example.com")
    me = client.get("/api/v1/auth/me", headers=headers(pair)).json()
    assert me["role"] == "ORG_ADMIN"
    assert me["email"] == "admin@alpha.example.com"
    assert "password_hash" not in me
    organization = client.get("/api/v1/organizations/current", headers=headers(pair)).json()
    assert organization["id"] == me["organization_id"]
    assert organization["plan"] == "FREE"
    logged_in = login(client, "ADMIN@alpha.example.com")
    assert client.get("/api/v1/auth/me", headers=headers(logged_in)).status_code == 200
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "unknown@example.com", "password": PASSWORD}
    )
    incorrect = client.post("/api/v1/auth/login", json={"email": me["email"], "password": "wrong"})
    assert unknown.status_code == incorrect.status_code == 401
    assert unknown.json()["error"]["message"] == incorrect.json()["error"]["message"]


def test_registration_atomic_duplicates_and_no_privilege_injection(client: TestClient) -> None:
    pair = register(client)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@alpha.example.com",
            "password": PASSWORD,
            "first_name": "A",
            "last_name": "B",
            "organization_name": "Rollback",
            "organization_slug": "rollback-org",
        },
    )
    assert response.status_code == 409
    # The duplicate-email transaction must not leave an orphan organization slug.
    register(client, "rollback-org")
    for extra in [
        {"role": "SUPER_ADMIN"},
        {"organization_id": str(uuid4())},
        {"plan": "ENTERPRISE"},
    ]:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "new@example.com",
                "password": PASSWORD,
                "first_name": "A",
                "last_name": "B",
                "organization_name": "New",
                "organization_slug": "new-org",
                **extra,
            },
        )
        assert response.status_code == 422
    denied = client.post(
        "/api/v1/users",
        headers=headers(pair),
        json={
            "email": "root@example.com",
            "password": PASSWORD,
            "first_name": "A",
            "last_name": "B",
            "role": "SUPER_ADMIN",
        },
    )
    assert denied.status_code == 403


def test_tenant_boundaries_and_spoofed_headers(client: TestClient) -> None:
    alpha, beta = register(client), register(client, "beta")
    b_user = client.get("/api/v1/auth/me", headers=headers(beta)).json()
    for path in [
        f"/api/v1/users/{b_user['id']}",
        f"/api/v1/organizations/{b_user['organization_id']}",
    ]:
        response = client.get(
            path, headers={**headers(alpha), "X-Organization-ID": b_user["organization_id"]}
        )
        assert response.status_code == 404
    assert (
        client.patch(
            f"/api/v1/users/{b_user['id']}", headers=headers(alpha), json={"status": "DISABLED"}
        ).status_code
        == 404
    )
    members = client.get("/api/v1/users", headers=headers(alpha)).json()
    assert len(members) == 1
    assert all(user["organization_id"] != b_user["organization_id"] for user in members)
    forged = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {alpha['access_token'][:-6]}forged"}
    )
    assert forged.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/users?limit=101", headers=headers(alpha)).status_code == 422


@pytest.mark.parametrize("role", ["REVIEWER", "ANALYST", "VIEWER", "AUDITOR", "ORG_ADMIN"])
def test_role_capabilities(client: TestClient, role: str) -> None:
    admin = register(client)
    create_member(client, admin, role)
    member = login(client, "member@example.com")
    assert client.get("/api/v1/auth/me", headers=headers(member)).status_code == 200
    assert client.get("/api/v1/users", headers=headers(member)).status_code == (
        200 if role in {"AUDITOR", "ORG_ADMIN"} else 403
    )
    response = client.post(
        "/api/v1/users",
        headers=headers(member),
        json={
            "email": "another@example.com",
            "password": PASSWORD,
            "first_name": "Another",
            "last_name": "User",
        },
    )
    assert response.status_code == (201 if role == "ORG_ADMIN" else 403)
    assert (
        Role(role) in PERMISSIONS["members:read"]
        if role in {"AUDITOR", "ORG_ADMIN"}
        else Role(role) not in PERMISSIONS["members:read"]
    )


def test_refresh_rotation_replay_and_revocation(client: TestClient) -> None:
    pair = register(client)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != pair["refresh_token"]
    assert client.get("/api/v1/auth/me", headers=headers(rotated)).status_code == 200
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert replay.status_code == 401
    # Replay revocation must survive the error transaction rollback.
    assert client.get("/api/v1/auth/me", headers=headers(rotated)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
        ).status_code
        == 401
    )
    separate = login(client, "admin@alpha.example.com")
    assert client.post("/api/v1/auth/logout", headers=headers(separate)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers(separate)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": separate["refresh_token"]}
        ).status_code
        == 401
    )


def test_access_change_revokes_session_and_blocks_disabled_login(client: TestClient) -> None:
    admin = register(client)
    user = create_member(client, admin, "ORG_ADMIN")
    member = login(client, "member@example.com")
    own = client.get("/api/v1/auth/me", headers=headers(admin)).json()
    assert (
        client.patch(
            f"/api/v1/users/{own['id']}", headers=headers(admin), json={"role": "VIEWER"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/users/{user['id']}", headers=headers(admin), json={"role": "SUPER_ADMIN"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/users/{user['id']}", headers=headers(admin), json={"status": "DISABLED"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=headers(member)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": member["refresh_token"]}
        ).status_code
        == 401
    )


def test_only_hashes_are_persisted(client: TestClient) -> None:
    pair = register(client)

    async def inspect() -> None:
        async with client.app.state.test_factory() as db:  # type: ignore[union-attr]
            user = await db.scalar(select(User))
            token = await db.scalar(select(RefreshToken))
            assert user.password_hash != PASSWORD
            assert password_hasher.verify(PASSWORD, user.password_hash)
            assert token.token_hash == refresh_digest(pair["refresh_token"])
            assert token.token_hash != pair["refresh_token"]

    asyncio.run(inspect())


def test_expired_session(client: TestClient) -> None:
    pair = register(client)

    async def expire() -> None:
        async with client.app.state.test_factory() as db, db.begin():  # type: ignore[union-attr]
            session = await db.scalar(select(AuthSession))
            session.expires_at = utcnow() - timedelta(seconds=1)

    asyncio.run(expire())
    assert client.get("/api/v1/auth/me", headers=headers(pair)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )


def test_jwt_security_and_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(jwt_secret=SecretStr("safe-test-key-" * 4))
    user_id, org_id, sid = uuid4(), uuid4(), uuid4()
    token = access_token(user_id, org_id, sid, settings)
    assert decode_access(token, settings).org == org_id
    data = jwt.decode(token, options={"verify_signature": False})
    for changes in [
        {"exp": 1},
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"typ": "refresh"},
        {"org": "invalid"},
    ]:
        with pytest.raises(HTTPException) as failure:
            decode_access(
                jwt.encode(
                    {**data, **changes}, settings.jwt_secret.get_secret_value(), algorithm="HS256"
                ),
                settings,
            )
        assert failure.value.status_code == 401
    with pytest.raises(HTTPException):
        decode_access(jwt.encode(data, "other-key-" * 8, algorithm="HS256"), settings)
    monkeypatch.delenv("LEASEGUARD_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(environment="production", _env_file=None)
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SecretStr("short"))


def test_rate_limit_is_enforced_and_fail_closed() -> None:
    app = create_app()
    with TestClient(app) as client:
        with patch.object(app.state.redis, "eval", new=AsyncMock(return_value=31)):
            response = client.post(
                "/api/v1/auth/login", json={"email": "x@example.com", "password": PASSWORD}
            )
            assert response.status_code == 429
            assert response.headers["retry-after"] == "60"
        with patch.object(
            app.state.redis, "eval", new=AsyncMock(side_effect=OSError("private-secret"))
        ):
            response = client.post(
                "/api/v1/auth/login", json={"email": "x@example.com", "password": PASSWORD}
            )
            assert response.status_code == 503
            assert "private-secret" not in response.text


def test_closed_registration(client: TestClient) -> None:
    client.app.state.settings.registration_enabled = False  # type: ignore[union-attr]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "closed@example.com",
            "password": PASSWORD,
            "first_name": "A",
            "last_name": "B",
            "organization_name": "Closed",
            "organization_slug": "closed",
        },
    )
    assert response.status_code == 403


def test_signed_wrong_tenant_and_suspended_org(client: TestClient) -> None:
    from app.auth.models import Organization

    pair = register(client)
    claims = jwt.decode(pair["access_token"], options={"verify_signature": False})
    config = client.app.state.settings  # type: ignore[union-attr]
    # Even a correctly signed token cannot combine another tenant with this user/session.
    forged = jwt.encode(
        {**claims, "org": str(uuid4())}, config.jwt_secret.get_secret_value(), algorithm="HS256"
    )
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code
        == 401
    )

    async def suspend() -> None:
        async with client.app.state.test_factory() as db, db.begin():  # type: ignore[union-attr]
            organization = await db.scalar(select(Organization))
            organization.status = "SUSPENDED"

    asyncio.run(suspend())
    assert client.get("/api/v1/auth/me", headers=headers(pair)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "admin@alpha.example.com", "password": PASSWORD}
        ).status_code
        == 401
    )


def test_password_whitespace_is_significant(client: TestClient) -> None:
    pair = register(client, password=f"  {PASSWORD}  ")
    assert client.get("/api/v1/auth/me", headers=headers(pair)).status_code == 200
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "admin@alpha.example.com", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert login(client, "admin@alpha.example.com", f"  {PASSWORD}  ")["access_token"]


def test_database_foreign_keys_prevent_tenant_mismatch(client: TestClient) -> None:
    from sqlalchemy.exc import IntegrityError

    alpha, beta = register(client), register(client, "beta")
    alpha_user = client.get("/api/v1/auth/me", headers=headers(alpha)).json()
    beta_user = client.get("/api/v1/auth/me", headers=headers(beta)).json()

    async def insert_mismatches() -> None:
        from uuid import UUID

        async with client.app.state.test_factory() as db:  # type: ignore[union-attr]
            with pytest.raises(IntegrityError):
                async with db.begin():
                    db.add(
                        AuthSession(
                            user_id=UUID(alpha_user["id"]),
                            organization_id=UUID(beta_user["organization_id"]),
                            expires_at=utcnow() + timedelta(days=1),
                        )
                    )
                    await db.flush()
            with pytest.raises(IntegrityError):
                async with db.begin():
                    existing = await db.scalar(
                        select(AuthSession).where(AuthSession.user_id == UUID(alpha_user["id"]))
                    )
                    db.add(
                        RefreshToken(
                            session_id=existing.id,
                            organization_id=UUID(beta_user["organization_id"]),
                            token_hash="0" * 64,
                        )
                    )
                    await db.flush()

    asyncio.run(insert_mismatches())


def test_role_change_requires_reauthentication(client: TestClient) -> None:
    admin = register(client)
    member = create_member(client, admin, "ORG_ADMIN")
    previous = login(client, "member@example.com")
    assert (
        client.patch(
            f"/api/v1/users/{member['id']}", headers=headers(admin), json={"role": "VIEWER"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me", headers=headers(previous)).status_code == 401
    next_session = login(client, "member@example.com")
    assert client.get("/api/v1/auth/me", headers=headers(next_session)).json()["role"] == "VIEWER"
    assert client.get("/api/v1/users", headers=headers(next_session)).status_code == 403
