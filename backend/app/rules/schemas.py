from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Outcome = Literal["PASS", "FAIL", "INDETERMINATE", "NOT_APPLICABLE", "DISABLED"]


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: Literal[
        "EXACT_SOURCE",
        "EXPLICIT_INTERPRETATION",
        "LITERAL_CONSISTENCY",
        "POSITIVE_DURATION",
        "MONEY_SCALE",
        "DATE_ORDER",
        "TERMINATION_NOTICE",
        "RENEWAL_LINK",
    ]
    scope: Literal["FIELD", "EXTRACTION"]


class RuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    description: str
    version: str
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    enabled: bool = True
    conditions: Condition
    actions: tuple[Literal["ANNOTATE", "REQUIRE_REVIEW"], ...] = ("ANNOTATE", "REQUIRE_REVIEW")
    created_at: date
    updated_at: date


class RulePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    engine_revision: Literal["deterministic-rules-v1"] = "deterministic-rules-v1"
    enabled: bool = True
    rules: tuple[RuleDefinition, ...]


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rule_id: str
    rule_version: str
    severity: Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
    outcome: Outcome
    reason: str
    field_indices: tuple[int, ...] = ()
    chunk_ids: tuple[UUID, ...] = ()


class FieldOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: int = Field(ge=0, le=23)
    status: Literal["PASS", "FAIL", "INDETERMINATE", "DISABLED"]
    passed: bool | None
    rules: tuple[str, ...]


class RuleReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy: RulePolicy
    policy_sha256: str
    input_sha256: str
    outcome: Literal["PASS", "FAIL", "INDETERMINATE", "DISABLED"]
    findings: tuple[Finding, ...] = Field(max_length=200)
    field_outcomes: tuple[FieldOutcome, ...] = Field(max_length=24)
    evaluated_at: datetime
    decision: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"
    legal_compliance_established: Literal[False] = False
    interpretation: str = "Deterministic checks of partial model proposals, not verification of lease facts, legal compliance or document completeness."
