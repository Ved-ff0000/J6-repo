"""Provider contracts use explicit HTTP mock transports. No real AI success is fabricated."""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from test_auth import create_member, headers, login, register

from app.ai.embeddings import create_embedding_provider
from app.ai.interfaces import (
    EmbeddingProvider,
    GenerationRequest,
    LLMProvider,
    Message,
    ProviderError,
)
from app.ai.openai import create_llm_provider
from app.ai.prompts import DiagnosticOutput, diagnostic_request
from app.ai.router import GATE, REFRESH, RELEASE
from app.ai.transport import post_json
from app.core.config import Settings


def settings(**changes):
    return Settings(llm_enabled=True, llm_api_key=SecretStr("synthetic-not-a-real-key"), **changes)


def reply(content=None, **changes):
    output = {
        "id": "chatcmpl-synthetic",
        "model": "gpt-4.1-mini-2025-04-14",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content
                    if content is not None
                    else json.dumps(
                        {"marker": "LEASEGUARD_SYNTHETIC_PROVIDER_PROBE", "synthetic_only": True}
                    ),
                },
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
    }
    output.update(changes)
    return output


def transport_patch(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return patch("httpx.AsyncClient", return_value=client)


def test_interfaces_and_real_structured_http_contract():
    async def scenario():
        provider = create_llm_provider(settings())
        assert isinstance(provider, LLMProvider)
        assert isinstance(create_embedding_provider(Settings()), EmbeddingProvider)
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=reply())

        with transport_patch(handler):
            output = await provider.generate_structured(diagnostic_request(), DiagnosticOutput)
        assert output.data.synthetic_only is True and output.result.usage.total_tokens == 60
        body = json.loads(calls[0].content)
        assert calls[0].url.path == "/v1/chat/completions"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        assert (
            body["store"] is False and "tools" not in body and body["max_completion_tokens"] == 256
        )
        assert (
            output.result.configured_model == "gpt-4.1-mini"
            and output.result.returned_model.endswith("2025-04-14")
        )

    asyncio.run(scenario())


def test_plain_generation_and_missing_usage_is_unknown():
    async def scenario():
        with transport_patch(
            lambda _: httpx.Response(200, json=reply("Synthetic text", usage=None))
        ):
            result = await create_llm_provider(settings()).generate(diagnostic_request())
        assert result.content == "Synthetic text" and result.usage is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "PROVIDER_AUTH_FAILED"),
        (403, "PROVIDER_AUTH_FAILED"),
        (429, "PROVIDER_RATE_LIMITED"),
        (500, "PROVIDER_UNAVAILABLE"),
        (302, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_upstream_statuses_are_sanitized_and_never_retried(status, code):
    calls = []

    async def scenario():
        def handler(request):
            calls.append(request)
            return httpx.Response(
                status,
                text="private upstream secret",
                headers={"Location": "https://forbidden.invalid"},
            )

        with transport_patch(handler), pytest.raises(ProviderError, match=code):
            await create_llm_provider(settings()).generate(diagnostic_request())

    asyncio.run(scenario())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ("tool", "PROVIDER_UNSUPPORTED_OUTPUT"),
        ("refusal", "PROVIDER_REFUSED"),
        ("length", "PROVIDER_TRUNCATED"),
        ("bad_usage", "PROVIDER_INVALID_RESPONSE"),
        ("empty", "PROVIDER_INVALID_RESPONSE"),
        ("extra_choices", "PROVIDER_INVALID_RESPONSE"),
    ],
)
def test_invalid_model_outputs_do_not_become_success(change, code):
    data = reply()
    if change == "tool":
        data["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "do_not_execute"}}]
    if change == "refusal":
        data["choices"][0]["message"]["refusal"] = "private refusal text"
    if change == "length":
        data["choices"][0]["finish_reason"] = "length"
    if change == "bad_usage":
        data["usage"]["prompt_tokens"] = True
    if change == "empty":
        data["choices"][0]["message"]["content"] = ""
    if change == "extra_choices":
        data["choices"].append(data["choices"][0])

    async def scenario():
        with (
            transport_patch(lambda _: httpx.Response(200, json=data)),
            pytest.raises(ProviderError, match=code),
        ):
            await create_llm_provider(settings()).generate(diagnostic_request())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "```json\n{}\n```",
        '{"marker":"LEASEGUARD_SYNTHETIC_PROVIDER_PROBE","synthetic_only":false}',
        '{"marker":"LEASEGUARD_SYNTHETIC_PROVIDER_PROBE","synthetic_only":true,"approved":true}',
        '{"marker":"LEASEGUARD_SYNTHETIC_PROVIDER_PROBE","synthetic_only":true,"synthetic_only":false}',
    ],
)
def test_structured_output_is_locally_validated(content):
    async def scenario():
        with (
            transport_patch(lambda _: httpx.Response(200, json=reply(content))),
            pytest.raises(ProviderError, match="PROVIDER_SCHEMA_INVALID"),
        ):
            await create_llm_provider(settings()).generate_structured(
                diagnostic_request(), DiagnosticOutput
            )

    asyncio.run(scenario())


def test_limits_disabled_and_timeout_are_fail_closed():
    async def scenario():
        with (
            patch("httpx.AsyncClient") as network,
            pytest.raises(ProviderError, match="PROVIDER_DISABLED"),
        ):
            await create_llm_provider(Settings()).generate(diagnostic_request())
        network.assert_not_called()
        with (
            patch("httpx.AsyncClient") as network,
            pytest.raises(ProviderError, match="PROVIDER_INPUT_LIMIT"),
        ):
            await create_llm_provider(settings(llm_max_output_tokens=64)).generate(
                diagnostic_request()
            )
        network.assert_not_called()

        def slow(request):
            raise httpx.ReadTimeout("private timeout")

        with transport_patch(slow), pytest.raises(ProviderError, match="PROVIDER_TIMEOUT"):
            await create_llm_provider(settings()).generate(diagnostic_request())
        with (
            transport_patch(lambda _: httpx.Response(200, content=b"x" * 100)),
            pytest.raises(ProviderError, match="PROVIDER_RESPONSE_TOO_LARGE"),
        ):
            await post_json("https://example.com", SecretStr("test"), {}, timeout=1, max_bytes=20)

    asyncio.run(scenario())


def test_nested_strict_schema_and_bad_schema_before_network():
    class Item(BaseModel):
        value: int

    class Envelope(BaseModel):
        model_config = ConfigDict(extra="forbid")
        item: Item
        note: str | None = None

    class Unsupported(BaseModel):
        values: dict[str, str]

    async def scenario():
        with (
            transport_patch(
                lambda _: httpx.Response(
                    200, json=reply('{"item":{"value":1,"unexpected":2},"note":null}')
                )
            ),
            pytest.raises(ProviderError, match="PROVIDER_SCHEMA_INVALID"),
        ):
            await create_llm_provider(settings()).generate_structured(
                diagnostic_request(), Envelope
            )
        with (
            patch("httpx.AsyncClient") as network,
            pytest.raises(ProviderError, match="PROVIDER_SCHEMA_UNSUPPORTED"),
        ):
            await create_llm_provider(settings()).generate_structured(
                diagnostic_request(), Unsupported
            )
        network.assert_not_called()

    asyncio.run(scenario())


def test_provider_settings_and_message_budgets():
    for data in (
        {"llm_enabled": True},
        {"llm_enabled": True, "llm_api_key": SecretStr("test"), "llm_url": "http://example.com"},
        {"llm_url": "https://user:secret@example.com"},
        {"llm_model": "bad model with spaces"},
    ):
        with pytest.raises(ValidationError):
            Settings(**data)
    with pytest.raises(ValidationError):
        GenerationRequest(
            messages=(Message(role="user", content="x" * 32000), Message(role="user", content="y"))
        )
    with pytest.raises(ValidationError):
        GenerationRequest(
            messages=(
                Message(role="user", content="one"),
                Message(role="system", content="late system"),
            )
        )


def test_embedding_profile_is_preserved_by_interface_refactor():
    spec = {"revision": "embedding-v1", "provider": "hashing", "dimensions": 512}
    expected = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    assert create_embedding_provider(Settings()).profile == expected


class TestLimiter:
    """Test-only Lua contract, not a substitute for real Redis integration."""

    __test__ = False

    def __init__(self):
        self.counts = {}
        self.leases = {}

    async def eval(self, script, numkeys, *args):
        if script == GATE:
            count, lease, limit, _window, token, _ttl, *cost = args
            units = cost[0] if cost else 1
            if lease in self.leases:
                return -1
            if self.counts.get(count, 0) + units > limit:
                return -2
            self.counts[count] = self.counts.get(count, 0) + units
            self.leases[lease] = token
            return 1
        if script == REFRESH:
            lease, token, _ttl = args
            return int(self.leases.get(lease) == token)
        if script == RELEASE:
            lease, token = args
            if self.leases.get(lease) == token:
                del self.leases[lease]
                return 1
            return 0
        raise AssertionError("Unexpected limiter script")


@pytest.fixture
def ai_client(client):
    client.app.state.session_factory = client.app.state.test_factory
    client.app.state.settings.llm_enabled = True
    client.app.state.settings.llm_api_key = SecretStr("synthetic-not-a-real-key")
    client.app.state.redis = TestLimiter()
    return client


def probe(client, pair, key=None, **extra):
    return client.post(
        "/api/v1/ai/probes",
        headers=headers(pair),
        json={"request_key": str(key or uuid4()), "acknowledge_external_request": True, **extra},
    )


def test_probe_records_real_response_metadata_and_replays_without_network(ai_client):
    client = ai_client
    pair = register(client)
    key = uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=reply())

    with transport_patch(handler):
        first = probe(client, pair, key)
    assert first.status_code == 200, first.text
    run = first.json()
    assert run["status"] == "SUCCEEDED" and run["total_tokens"] == 60 and run["output_sha256"]
    assert "content" not in run and "synthetic-not-a-real-key" not in first.text
    replay = probe(client, pair, key)
    assert replay.status_code == 200 and replay.json()["replayed"] and len(calls) == 1
    assert (
        client.get(f"/api/v1/ai/probes/{run['id']}", headers=headers(pair)).json()["id"]
        == run["id"]
    )
    assert client.get("/api/v1/ai/probes", headers=headers(pair)).json()[0]["id"] == run["id"]
    assert first.headers["cache-control"] == "no-store"


def test_probe_rbac_acknowledgement_and_disabled_policy(ai_client):
    client = ai_client
    pair = register(client)
    create_member(client, pair, "ANALYST")
    analyst = login(client, "member@example.com")
    assert probe(client, analyst).status_code == 403
    assert client.get("/api/v1/ai/config", headers=headers(analyst)).status_code == 403
    assert probe(client, pair, acknowledge_external_request=False).status_code == 422
    assert probe(client, pair, prompt="do anything").status_code == 422
    assert probe(client, pair, organization_id=str(uuid4())).status_code == 422
    client.app.state.settings.llm_enabled = False
    with patch("httpx.AsyncClient") as network:
        assert probe(client, pair).status_code == 503
    network.assert_not_called()
    assert client.get("/api/v1/ai/config", headers=headers(pair)).json()["enabled"] is False


def test_failed_probe_is_persisted_sanitized_and_cannot_be_retried_by_replay(ai_client):
    client = ai_client
    pair = register(client)
    key = uuid4()
    with transport_patch(lambda _: httpx.Response(401, text="private-upstream-secret")):
        result = probe(client, pair, key)
    assert (
        result.status_code == 503
        and "PROVIDER_AUTH_FAILED" in result.text
        and "private-upstream-secret" not in result.text
    )
    history = client.get("/api/v1/ai/probes", headers=headers(pair)).json()
    assert history[0]["status"] == "FAILED" and history[0]["input_tokens"] is None
    assert probe(client, pair, key).json()["status"] == "FAILED"


def test_probe_limiter_outage_and_org_scope(ai_client):
    client = ai_client
    alpha = register(client)
    beta = register(client, "beta")
    client.app.state.settings.llm_probe_limit = 1
    with transport_patch(lambda _: httpx.Response(200, json=reply())):
        first = probe(client, alpha)
    assert first.status_code == 200
    assert probe(client, alpha).status_code == 429
    assert (
        client.get(f"/api/v1/ai/probes/{first.json()['id']}", headers=headers(beta)).status_code
        == 404
    )
    assert client.get("/api/v1/ai/probes", headers=headers(beta)).json() == []
    with transport_patch(lambda _: httpx.Response(200, json=reply())):
        assert probe(client, beta).status_code == 200
    client.app.state.redis.eval = AsyncMock(side_effect=RedisConnectionError("private redis error"))
    with patch("httpx.AsyncClient") as network:
        result = probe(client, beta)
    assert result.status_code == 503 and "private redis error" not in result.text
    network.assert_not_called()


def test_schema_property_names_are_not_treated_as_schema_keywords():
    from app.ai.openai import strict_schema

    class Fields(BaseModel):
        default: str
        const: str

    schema = strict_schema(Fields)
    assert set(schema["properties"]) == {"default", "const"}
    assert set(schema["required"]) == {"default", "const"}


def test_nonfinite_json_transport_is_rejected():
    async def scenario():
        with (
            transport_patch(lambda _: httpx.Response(200, content=b'{"unexpected":1e999}')),
            pytest.raises(ProviderError, match="PROVIDER_INVALID_RESPONSE"),
        ):
            await post_json("https://example.com", SecretStr("test"), {}, timeout=1, max_bytes=1000)

    asyncio.run(scenario())


def test_concurrent_probe_is_busy_without_another_provider_call(ai_client):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client = ai_client
    pair = register(client)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return httpx.Response(200, json=reply())

    with transport_patch(handler), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(probe, client, pair)
        try:
            assert entered.wait(3)
            assert probe(client, pair).status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 200
    assert len(calls) == 1


def test_started_request_replay_is_not_paid_again(ai_client):
    from app.ai.models import ProviderRun

    client = ai_client
    pair = register(client)
    key = uuid4()
    with transport_patch(lambda _: httpx.Response(200, json=reply())):
        result = probe(client, pair, key)

    async def interrupt():
        async with client.app.state.test_factory() as db, db.begin():
            from uuid import UUID

            run = await db.get(ProviderRun, UUID(result.json()["id"]))
            run.status = "STARTED"
            run.finished_at = None

    client.portal.call(interrupt)
    with patch("httpx.AsyncClient") as network:
        replay = probe(client, pair, key)
    assert (
        replay.status_code == 202
        and replay.json()["replayed"]
        and replay.json()["status"] == "STARTED"
    )
    network.assert_not_called()


@pytest.mark.parametrize("scope", ["user", "organization", "role", "session", "expiry"])
def test_authorization_revoked_during_paid_call_cannot_publish_success(ai_client, scope):
    from datetime import timedelta

    from sqlalchemy import select

    from app.ai.models import ProviderRun
    from app.auth.models import AuthSession, Organization, User
    from app.auth.security import utcnow

    client = ai_client
    pair = register(client)

    async def handler(_):
        async with client.app.state.test_factory() as db, db.begin():
            actor = await db.scalar(select(User).where(User.email == "admin@alpha.example.com"))
            if scope == "user":
                actor.status = "DISABLED"
            if scope == "organization":
                org = await db.get(Organization, actor.organization_id)
                org.status = "SUSPENDED"
            if scope == "role":
                actor.role = "VIEWER"
            if scope in {"session", "expiry"}:
                session = await db.scalar(
                    select(AuthSession).where(AuthSession.user_id == actor.id)
                )
                if scope == "session":
                    session.revoked_at = utcnow()
                else:
                    session.expires_at = utcnow() - timedelta(seconds=1)
        return httpx.Response(200, json=reply())

    with transport_patch(handler):
        result = probe(client, pair)
    assert result.status_code == 403 and "AUTHORIZATION_REVOKED" in result.text

    async def recorded():
        async with client.app.state.test_factory() as db:
            run = await db.scalar(select(ProviderRun))
            assert (
                run.status == "FAILED"
                and run.error_code == "AUTHORIZATION_REVOKED"
                and run.total_tokens == 60
            )

    client.portal.call(recorded)


def test_compressed_http_responses_are_rejected_before_decompression():
    async def scenario():
        with (
            transport_patch(
                lambda _: httpx.Response(
                    200,
                    headers={"Content-Encoding": "gzip"},
                    stream=httpx.ByteStream(b"not-decoded"),
                )
            ),
            pytest.raises(ProviderError, match="PROVIDER_INVALID_RESPONSE"),
        ):
            await post_json("https://example.com", SecretStr("test"), {}, timeout=1, max_bytes=1000)

    asyncio.run(scenario())
