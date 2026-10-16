"""PostgreSQL-only identity and concurrency coverage; CI enables this suite."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.auth.models import Organization
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires migrated PostgreSQL and Redis; CI enables integration tests",
)


def test_postgres_tenant_isolation_and_concurrent_rotation() -> None:
    slug = f"ci-{uuid4().hex}"
    settings = Settings(auth_rate_limit=1000)
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            pairs = []
            for suffix in ("alpha", "beta"):
                response = client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": f"{suffix}@{slug}.example.com",
                        "password": "Correct-horse-lease-42",
                        "first_name": "CI",
                        "last_name": "Admin",
                        "organization_name": f"{slug}-{suffix}",
                        "organization_slug": f"{slug}-{suffix}",
                    },
                )
                assert response.status_code == 201, response.text
                pairs.append(response.json())
            headers = [{"Authorization": f"Bearer {pair['access_token']}"} for pair in pairs]
            users = [client.get("/api/v1/auth/me", headers=header).json() for header in headers]
            assert (
                client.get(f"/api/v1/users/{users[1]['id']}", headers=headers[0]).status_code == 404
            )
            assert client.get("/api/v1/users", headers=headers[0]).json()[0]["id"] == users[0]["id"]

            def refresh() -> object:
                return client.post(
                    "/api/v1/auth/refresh", json={"refresh_token": pairs[0]["refresh_token"]}
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda _: refresh(), range(2)))
            assert sorted(response.status_code for response in responses) == [200, 401]
            # One refresh wins, one detects replay and revokes the entire session family.
            successful = next(
                response.json() for response in responses if response.status_code == 200
            )
            assert (
                client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {successful['access_token']}"},
                ).status_code
                == 401
            )
            assert client.get("/api/v1/auth/me", headers=headers[1]).status_code == 200
            assert client.post("/api/v1/auth/logout", headers=headers[1]).status_code == 204
            assert client.get("/api/v1/auth/me", headers=headers[1]).status_code == 401
    finally:

        async def cleanup() -> None:
            engine = create_engine(settings)
            try:
                async with engine.begin() as connection:
                    # Scope cleanup to this test's unique organization prefix, including partial failures.
                    await connection.execute(
                        delete(Organization).where(Organization.slug.like(f"{slug}-%"))
                    )
            finally:
                await engine.dispose()

        asyncio.run(cleanup())


def test_live_redis_auth_limit() -> None:
    app = create_app(Settings(auth_rate_limit=2))
    with TestClient(app) as client:
        data = {"email": f"unknown-{uuid4().hex}@example.com", "password": "Correct-horse-lease-42"}
        assert client.post("/api/v1/auth/login", json=data).status_code == 401
        assert client.post("/api/v1/auth/login", json=data).status_code == 401
        response = client.post("/api/v1/auth/login", json=data)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
