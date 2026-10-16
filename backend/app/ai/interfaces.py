from typing import Generic, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """Safe fixed code only. Never serialize upstream response bodies or exception strings."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=32000)


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[Message, ...] = Field(min_length=1, max_length=20)
    temperature: float = Field(default=0, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=256, ge=1, le=2048, strict=True)

    @model_validator(mode="after")
    def bounded(self) -> "GenerationRequest":
        if sum(len(message.content) for message in self.messages) > 32000:
            raise ValueError("Message budget exceeded")
        if sum(message.role == "system" for message in self.messages) > 1 or any(
            message.role == "system" for message in self.messages[1:]
        ):
            raise ValueError("At most one leading system instruction is supported")
        return self


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: StrictInt = Field(ge=0, le=10000000)
    output_tokens: StrictInt = Field(ge=0, le=10000000)
    total_tokens: StrictInt = Field(ge=0, le=20000000)

    @model_validator(mode="after")
    def total(self) -> "TokenUsage":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("Inconsistent reported usage")
        return self


class GenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    configured_model: str
    returned_model: str
    model_version: str | None = None  # OpenAI chat has no separate verified version field.
    response_id: str
    content: str
    usage: TokenUsage | None
    duration_ms: float = Field(ge=0, allow_inf_nan=False)


class StructuredGeneration(BaseModel, Generic[T]):  # noqa: UP046 - test sandbox also supports Python 3.11
    model_config = ConfigDict(extra="forbid", frozen=True)
    result: GenerationResult
    data: T


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    profile: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
    async def generate_structured(
        self, request: GenerationRequest, response_model: type[T]
    ) -> StructuredGeneration[T]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    profile: str
    collection: str
    label: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
