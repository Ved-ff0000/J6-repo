"""Bounded snapshots of proposal comparisons, never authority or approval decisions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.extraction.schemas import LeaseField


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    document_id: UUID
    version_id: UUID
    version_number: int = Field(ge=1)
    checksum: str
    version_created_at: datetime
    parent_document_id: UUID | None
    document_type: str


class ComparisonFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group: str
    field: str
    outcome: Literal["POTENTIAL_CONFLICT", "MATCH", "INDETERMINATE", "NOT_OBSERVED"]
    reason: str
    left: tuple[LeaseField, ...] = Field(max_length=24)
    right: tuple[LeaseField, ...] = Field(max_length=24)


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    engine_revision: Literal["proposal-comparison-v1"] = "proposal-comparison-v1"
    relationship: Literal["DOCUMENT_VERSIONS", "LINKED_AMENDMENT"]
    left_source: Source
    right_source: Source
    left_input_sha256: str
    right_input_sha256: str
    evaluated_at: datetime
    findings: tuple[ComparisonFinding, ...] = Field(max_length=48)
    outcome: Literal["POTENTIAL_CONFLICTS_DETECTED", "NO_DIFFERENCES_OBSERVED", "INDETERMINATE"]
    latest_uploaded_version_id: UUID | None
    authority_status: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    decision: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    completeness_established: Literal[False] = False
    previous_values_overwritten: Literal[False] = False
