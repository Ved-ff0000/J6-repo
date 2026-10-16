from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.ai.interfaces import TokenUsage
from app.ai.router import ProbeIn
from app.extraction.schemas import LeaseExtractionResult
from app.rules.schemas import RuleReport
from app.search.router import Citation


class WorkflowIn(ProbeIn):
    """The inherited strict acknowledgement covers document-text egress, not just diagnostics."""

    extraction_mode: Literal["CANDIDATES", "LEASE_FIELDS"] = "CANDIDATES"


class Classification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["LEASE", "AMENDMENT", "OTHER", "UNKNOWN"]


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["document_title", "clause_heading"]
    value: str = Field(min_length=1, max_length=200)
    chunk_id: UUID
    quote: str = Field(min_length=1, max_length=500)


class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[Candidate, ...] = Field(max_length=3)


class VerifiedCandidate(BaseModel):
    kind: Literal["document_title", "clause_heading"]
    value: str
    citation: Citation
    quote_start: int
    quote_end: int
    assertion: Literal["MODEL_PROPOSED_UNVERIFIED"] = "MODEL_PROPOSED_UNVERIFIED"


class CallMetadata(BaseModel):
    provider: str
    profile: str
    configured_model: str
    returned_model: str
    model_version: str | None
    response_id: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str
    temperature: float
    max_output_tokens: int
    usage: TokenUsage | None
    duration_ms: float
    recorded_at: datetime


class Confidence(BaseModel):
    semantic_score: None = None
    evidence_coverage: float | None = None
    explanation: str = "Exact evidence coverage is not legal correctness, extraction completeness or calibrated semantic confidence."


class WorkflowResult(BaseModel):
    classification: Classification
    candidates: list[VerifiedCandidate] = Field(max_length=3)
    confidence: Confidence
    source_chunk_count: int
    total_chunk_count: int
    retrieved_chunk_count: int
    decision: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    review_queue_created: bool = False
    scope: Literal["TITLE_AND_HEADING_CANDIDATES_ONLY", "LEASE_FIELD_PROPOSALS"] = (
        "TITLE_AND_HEADING_CANDIDATES_ONLY"
    )
    lease_extraction: LeaseExtractionResult | None = None
    rule_report: RuleReport | None = None


class StageEvent(BaseModel):
    name: str
    status: Literal["STARTED", "COMPLETED", "FAILED"]
    timestamp: datetime


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    version_id: UUID
    request_key: UUID
    revision: str
    extraction_mode: Literal["CANDIDATES", "LEASE_FIELDS"] = "CANDIDATES"
    status: Literal["STARTED", "REVIEW_REQUIRED", "FAILED"]
    stage: str
    trace: list[StageEvent]
    calls: list[CallMetadata]
    result: WorkflowResult | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
    replayed: bool = False
