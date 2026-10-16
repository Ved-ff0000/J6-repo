"""Finite HTTP JSON transport. No redirects, proxies, retry loops or raw-error logging."""

import asyncio
import json
import math
from typing import Any

import httpx
from pydantic import SecretStr

from app.ai.interfaces import ProviderError


def strict_json(content: str | bytes | bytearray) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError("Non-finite JSON")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite JSON number")
        return parsed

    return json.loads(
        content, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float
    )


async def post_json(
    url: str, key: SecretStr, payload: dict[str, object], *, timeout: float, max_bytes: int
) -> dict[str, Any]:
    try:
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client,
            client.stream(
                "POST",
                url,
                headers={
                    "Authorization": "Bearer " + key.get_secret_value(),
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                json=payload,
            ) as response,
        ):
            if response.status_code in (401, 403):
                raise ProviderError("PROVIDER_AUTH_FAILED")
            if response.status_code == 429:
                raise ProviderError("PROVIDER_RATE_LIMITED")
            if response.status_code != 200:
                raise ProviderError("PROVIDER_UNAVAILABLE")
            if response.headers.get("content-encoding", "identity").strip().lower() not in {
                "",
                "identity",
            }:
                raise ProviderError("PROVIDER_INVALID_RESPONSE")
            if int(response.headers.get("content-length", "0")) > max_bytes:
                raise ProviderError("PROVIDER_RESPONSE_TOO_LARGE")
            content = bytearray()
            async for part in response.aiter_bytes(chunk_size=8192):
                if len(content) + len(part) > max_bytes:
                    raise ProviderError("PROVIDER_RESPONSE_TOO_LARGE")
                content.extend(part)
            data = strict_json(content)
            if not isinstance(data, dict):
                raise ProviderError("PROVIDER_INVALID_RESPONSE")
            return data
    except ProviderError:
        raise
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ProviderError("PROVIDER_TIMEOUT") from exc
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProviderError("PROVIDER_INVALID_RESPONSE") from exc
    except Exception as exc:
        raise ProviderError("PROVIDER_UNAVAILABLE") from exc
