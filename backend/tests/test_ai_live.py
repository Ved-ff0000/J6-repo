"""Explicit opt-in PAID network test. Never runs merely because credentials exist."""

import asyncio
import os

import pytest

from app.ai.openai import create_llm_provider
from app.ai.prompts import DiagnosticOutput, diagnostic_request
from app.core.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("LEASEGUARD_RUN_PROVIDER_LIVE") != "1"
    or os.getenv("LEASEGUARD_PROVIDER_LIVE_ACK") != "synthetic-paid-probe",
    reason="Requires explicit paid synthetic-provider opt-in and private server credentials",
)


def test_actual_configured_llm_provider_structured_diagnostic():
    settings = Settings(llm_enabled=True)

    async def run():
        output = await create_llm_provider(settings).generate_structured(
            diagnostic_request(min(256, settings.llm_max_output_tokens)), DiagnosticOutput
        )
        assert output.data.synthetic_only is True
        assert output.result.provider == "openai" and output.result.returned_model

    asyncio.run(run())
