"""Versioned fixed synthetic diagnostic. No lease text, user prompt or tools are accepted."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.ai.interfaces import GenerationRequest, Message

PROMPT_NAME = "provider_diagnostic"
PROMPT_VERSION = "1.0.0"
PROMPT_DESCRIPTION = "Fixed synthetic structured-output connectivity diagnostic, not lease analysis"
PROMPT_CREATED_AT = "2026-10-16"
PROMPT_VARIABLES: tuple[str, ...] = ()


class DiagnosticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    marker: Literal["LEASEGUARD_SYNTHETIC_PROVIDER_PROBE"]
    synthetic_only: Literal[True]


def diagnostic_request(max_output_tokens: int = 256) -> GenerationRequest:
    return GenerationRequest(
        messages=(
            Message(
                role="system",
                content="You are running a synthetic provider connectivity diagnostic, not lease analysis. Return only JSON matching the supplied schema. Do not call tools, make claims about leases, or perform actions.",
            ),
            Message(
                role="user",
                content="Return marker LEASEGUARD_SYNTHETIC_PROVIDER_PROBE and synthetic_only true. This is a fixed synthetic test with no customer document content.",
            ),
        ),
        temperature=0,
        max_output_tokens=max_output_tokens,
    )
