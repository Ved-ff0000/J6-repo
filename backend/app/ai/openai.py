"""Actual Chat Completions adapter. No tools, retries, fallbacks or document actions."""

import hashlib
import json
import re
from time import perf_counter
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from app.ai.interfaces import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderError,
    StructuredGeneration,
    T,
    TokenUsage,
)
from app.ai.transport import post_json, strict_json
from app.core.config import Settings


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    if schema.get("type") != "object" or len(json.dumps(schema)) > 32768:
        raise ProviderError("PROVIDER_SCHEMA_UNSUPPORTED")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for keyword in ("$ref", "$dynamicRef"):
                if keyword in value and not str(value[keyword]).startswith("#/$defs/"):
                    raise ProviderError("PROVIDER_SCHEMA_UNSUPPORTED")
            value.pop("default", None)
            if "const" in value:
                value["enum"] = [value.pop("const")]
            if value.get("type") == "object":
                if "properties" not in value or value.get("additionalProperties") not in (
                    None,
                    False,
                ):
                    raise ProviderError("PROVIDER_SCHEMA_UNSUPPORTED")
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            # Walk schema positions, not arbitrary property-name maps or enum values.
            for keyword in (
                "$defs",
                "definitions",
                "properties",
                "patternProperties",
                "dependentSchemas",
            ):
                children = value.get(keyword)
                if isinstance(children, dict):
                    for child in children.values():
                        visit(child)
            for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
                children = value.get(keyword)
                if isinstance(children, list):
                    for child in children:
                        visit(child)
            for keyword in (
                "items",
                "additionalProperties",
                "not",
                "if",
                "then",
                "else",
                "contains",
                "propertyNames",
            ):
                child = value.get(keyword)
                if isinstance(child, dict):
                    visit(child)

    visit(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise ProviderError("PROVIDER_SCHEMA_UNSUPPORTED") from exc
    return schema


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        self.profile = hashlib.sha256(
            json.dumps(
                {
                    "provider": self.name,
                    "model": self.model,
                    "endpoint": settings.llm_url.rstrip("/"),
                    "revision": "chat-v1",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    async def _generate(
        self, request: GenerationRequest, response_format: dict[str, object] | None = None
    ) -> GenerationResult:
        if not self.settings.llm_enabled or self.settings.llm_api_key is None:
            raise ProviderError("PROVIDER_DISABLED")
        if request.max_output_tokens > self.settings.llm_max_output_tokens:
            raise ProviderError("PROVIDER_INPUT_LIMIT")
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [m.model_dump() for m in request.messages],
            "max_completion_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "stream": False,
            "n": 1,
            "store": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        start = perf_counter()
        response = await post_json(
            self.settings.llm_url.rstrip("/") + "/chat/completions",
            self.settings.llm_api_key,
            payload,
            timeout=self.settings.llm_timeout_seconds,
            max_bytes=1024 * 1024,
        )
        try:
            choices = response["choices"]
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or type(choices[0].get("index")) is not int
                or choices[0]["index"] != 0
            ):
                raise ValueError("Invalid choice")
            choice = choices[0]
            message = choice["message"]
            if message.get("refusal"):
                raise ProviderError("PROVIDER_REFUSED")
            if message.get("tool_calls") or message.get("function_call"):
                raise ProviderError("PROVIDER_UNSUPPORTED_OUTPUT")
            if choice.get("finish_reason") == "length":
                raise ProviderError("PROVIDER_TRUNCATED")
            if choice.get("finish_reason") != "stop" or message.get("role") != "assistant":
                raise ProviderError("PROVIDER_UNSUPPORTED_OUTPUT")
            content = message["content"]
            if not isinstance(content, str) or not content.strip() or len(content) > 32768:
                raise ValueError("Invalid content")
            model = response["model"]
            identifier = response["id"]
            if (
                not isinstance(model, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}", model)
                or not isinstance(identifier, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", identifier)
            ):
                raise ValueError("Invalid metadata")
            raw = response.get("usage")
            usage = (
                None
                if raw is None
                else TokenUsage(
                    input_tokens=raw["prompt_tokens"],
                    output_tokens=raw["completion_tokens"],
                    total_tokens=raw["total_tokens"],
                )
            )
            return GenerationResult(
                provider=self.name,
                configured_model=self.model,
                returned_model=model,
                response_id=identifier,
                content=content,
                usage=usage,
                duration_ms=(perf_counter() - start) * 1000,
            )
        except ProviderError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ProviderError("PROVIDER_INVALID_RESPONSE") from exc

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return await self._generate(request)

    async def generate_structured(
        self, request: GenerationRequest, response_model: type[T]
    ) -> StructuredGeneration[T]:
        schema = strict_schema(response_model)
        result = await self._generate(
            request,
            {
                "type": "json_schema",
                "json_schema": {"name": "leaseguard_output", "strict": True, "schema": schema},
            },
        )
        try:
            decoded = strict_json(result.content)
            Draft202012Validator(schema).validate(decoded)
            data = response_model.model_validate_json(result.content, strict=True)
            return StructuredGeneration(result=result, data=data)
        except Exception as exc:
            raise ProviderError("PROVIDER_SCHEMA_INVALID") from exc


class DisabledProvider:
    name = "disabled"
    model = "disabled"
    profile = "disabled"

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        raise ProviderError("PROVIDER_DISABLED")

    async def generate_structured(
        self, request: GenerationRequest, response_model: type[T]
    ) -> StructuredGeneration[T]:
        raise ProviderError("PROVIDER_DISABLED")


def create_llm_provider(settings: Settings) -> LLMProvider:
    return OpenAIProvider(settings) if settings.llm_enabled else DisabledProvider()
