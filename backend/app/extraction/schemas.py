"""Sparse typed proposals across all twelve Phase 10 groups; omission never proves absence."""

from collections.abc import Iterator
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.search.router import Citation

SCHEMA_VERSION = "lease-extraction-v1"
GroupName = Literal[
    "parties",
    "dates",
    "rent",
    "renewals",
    "termination",
    "notices",
    "obligations",
    "insurance",
    "maintenance",
    "assignment",
    "cam",
    "escalation",
]
GROUPS: tuple[GroupName, ...] = (
    "parties",
    "dates",
    "rent",
    "renewals",
    "termination",
    "notices",
    "obligations",
    "insurance",
    "maintenance",
    "assignment",
    "cam",
    "escalation",
)
Interpretation = Literal["EXPLICIT", "AMBIGUOUS", "CONDITIONAL", "CROSS_REFERENCE"]


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    field: str
    value: str = Field(min_length=1, max_length=300)
    chunk_id: UUID
    quote: str = Field(min_length=1, max_length=500)
    interpretation: Interpretation = "EXPLICIT"

    @field_validator("value", "quote")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence and value must not be blank")
        return value


class Party(Proposal):
    field: Literal["landlord", "tenant", "guarantor", "agent", "other"]


class LeaseDate(Proposal):
    field: Literal[
        "execution", "commencement", "expiration", "rent_commencement", "possession", "other"
    ]


class Rent(Proposal):
    field: Literal["base_rent", "security_deposit", "percentage_rent", "other"]


class Renewal(Proposal):
    field: Literal["option_count", "duration", "notice_period", "rent_basis", "conditions", "other"]


class Termination(Proposal):
    field: Literal["right", "trigger", "notice_period", "fee", "other"]


class Notice(Proposal):
    field: Literal["period", "recipient", "address", "method", "other"]


class Obligation(Proposal):
    field: Literal["responsibility", "deadline", "payment", "other"]


class Insurance(Proposal):
    field: Literal["coverage", "limit", "certificate", "additional_insured", "other"]


class Maintenance(Proposal):
    field: Literal["responsibility", "repair", "replacement", "cost", "other"]


class Assignment(Proposal):
    field: Literal["consent", "restriction", "subletting", "fee", "other"]


class CAM(Proposal):
    field: Literal["charge", "allocation", "cap", "exclusion", "reconciliation", "other"]


class Escalation(Proposal):
    field: Literal["method", "rate", "frequency", "cap", "floor", "other"]


class LeaseExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parties: tuple[Party, ...] = Field(default=(), max_length=6)
    dates: tuple[LeaseDate, ...] = Field(default=(), max_length=6)
    rent: tuple[Rent, ...] = Field(default=(), max_length=6)
    renewals: tuple[Renewal, ...] = Field(default=(), max_length=6)
    termination: tuple[Termination, ...] = Field(default=(), max_length=6)
    notices: tuple[Notice, ...] = Field(default=(), max_length=6)
    obligations: tuple[Obligation, ...] = Field(default=(), max_length=6)
    insurance: tuple[Insurance, ...] = Field(default=(), max_length=6)
    maintenance: tuple[Maintenance, ...] = Field(default=(), max_length=6)
    assignment: tuple[Assignment, ...] = Field(default=(), max_length=6)
    cam: tuple[CAM, ...] = Field(default=(), max_length=6)
    escalation: tuple[Escalation, ...] = Field(default=(), max_length=6)

    @model_validator(mode="after")
    def total_budget(self) -> "LeaseExtraction":
        if sum(1 for _ in proposal_items(self)) > 24:
            raise ValueError("At most 24 total lease field proposals")
        return self


def proposal_items(value: LeaseExtraction) -> Iterator[tuple[GroupName, Proposal]]:
    collections: tuple[tuple[GroupName, tuple[Proposal, ...]], ...] = (
        ("parties", value.parties),
        ("dates", value.dates),
        ("rent", value.rent),
        ("renewals", value.renewals),
        ("termination", value.termination),
        ("notices", value.notices),
        ("obligations", value.obligations),
        ("insurance", value.insurance),
        ("maintenance", value.maintenance),
        ("assignment", value.assignment),
        ("cam", value.cam),
        ("escalation", value.escalation),
    )
    for group, items in collections:
        for item in items:
            yield group, item


class NormalizedDate(BaseModel):
    kind: Literal["date"] = "date"
    iso_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class NormalizedMoney(BaseModel):
    kind: Literal["money"] = "money"
    amount: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Literal["month", "year", "week", "day", "one_time"] | None = None


class NormalizedDuration(BaseModel):
    kind: Literal["duration"] = "duration"
    amount: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    unit: Literal["days", "weeks", "months", "years"]


class NormalizedPercentage(BaseModel):
    kind: Literal["percentage"] = "percentage"
    percent: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")


NormalizedValue = NormalizedDate | NormalizedMoney | NormalizedDuration | NormalizedPercentage


class RuleValidation(BaseModel):
    status: Literal["NOT_RUN", "PASS", "FAIL", "INDETERMINATE", "DISABLED"] = "NOT_RUN"
    passed: bool | None = None
    rules: tuple[str, ...] = ()


class LeaseField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    group: GroupName
    field: str
    value: str
    interpretation: Interpretation
    normalized: NormalizedValue | None
    normalization_status: Literal["NORMALIZED", "REQUIRES_REVIEW", "NOT_APPLICABLE"]
    normalization_revision: Literal["literal-normalizer-v1"] = "literal-normalizer-v1"
    confidence: None = None
    status: Literal["MODEL_PROPOSED_UNVERIFIED"] = "MODEL_PROPOSED_UNVERIFIED"
    evidence_status: Literal["EXACT_QUOTE_MATCH"] = "EXACT_QUOTE_MATCH"
    citation: Citation
    evidence_text: str
    quote_start: int
    quote_end: int
    value_start: int
    value_end: int
    explanation: str = "Exact source text matched. Field role, interpretation, applicability and legal meaning remain unverified."
    rule_validation: RuleValidation = Field(default_factory=RuleValidation)


class GroupSummary(BaseModel):
    group: GroupName
    proposed_count: int = Field(ge=0, le=6)
    observation: Literal["PROPOSED_VALUES", "NOT_OBSERVED_IN_CONTEXT"]


class LeaseExtractionResult(BaseModel):
    schema_version: Literal["lease-extraction-v1"] = "lease-extraction-v1"
    fields: list[LeaseField] = Field(max_length=24)
    groups: list[GroupSummary] = Field(min_length=12, max_length=12)
    document_completeness: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    authoritative_lease_created: Literal[False] = False
