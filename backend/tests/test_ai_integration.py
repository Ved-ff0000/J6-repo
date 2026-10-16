"""Real PG/Redis limiter + ledger, mocked HTTP provider ONLY (not a live model test)."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from test_ai import probe, reply
from test_auth import headers, register

from app.ai.models import ProviderRun
from app.auth.models import Organization
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_INTEGRATION") != "1",
    reason="Requires migrated PostgreSQL/Redis; remote LLM calls remain mocked",
)


def test_postgres_ledger_real_redis_limits_and_idempotency():
    settings = Settings(
        auth_rate_limit=1000,
        llm_enabled=True,
        llm_api_key=SecretStr("synthetic-not-a-real-key"),
        llm_probe_limit=1,
    )
    slug = "provider-ci-" + uuid4().hex
    with TestClient(create_app(settings)) as client:
        try:
            pairs = [register(client, slug + suffix) for suffix in ("a", "b")]
            for pair in pairs:
                key = uuid4()
                entered = threading.Event()
                release = threading.Event()

                async def handler(_, entered=entered, release=release):
                    entered.set()
                    await asyncio.to_thread(release.wait, 10)
                    return httpx.Response(200, json=reply())

                with (
                    patch(
                        "httpx.AsyncClient",
                        return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                    ),
                    ThreadPoolExecutor(max_workers=1) as pool,
                ):
                    future = pool.submit(probe, client, pair, key)
                    try:
                        assert entered.wait(5)
                        assert probe(client, pair).status_code == 409
                    finally:
                        release.set()
                    result = future.result(timeout=10)
                assert result.status_code == 200, result.text
                assert result.json()["status"] == "SUCCEEDED"
                assert probe(client, pair, key).json()["replayed"] is True
                assert probe(client, pair).status_code == 429
            assert (
                client.get("/api/v1/ai/probes", headers=headers(pairs[0])).json()[0]["id"]
                != client.get("/api/v1/ai/probes", headers=headers(pairs[1])).json()[0]["id"]
            )
        finally:

            async def cleanup():
                engine = create_engine(settings)
                try:
                    async with engine.begin() as connection:
                        ids = select(Organization.id).where(
                            Organization.slug.in_([slug + suffix for suffix in ("a", "b")])
                        )
                        await connection.execute(
                            delete(ProviderRun).where(ProviderRun.organization_id.in_(ids))
                        )
                        await connection.execute(
                            delete(Organization).where(Organization.id.in_(ids))
                        )
                finally:
                    await engine.dispose()

            asyncio.run(cleanup())
