"""Per-run typed LangGraph; no global run state, checkpoint uploads, tools or auto-approval."""

from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Literal, Protocol
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langsmith.run_helpers import tracing_context
from pydantic import BaseModel, ConfigDict, Field

from app.auth.security import utcnow
from app.extraction.schemas import LeaseExtraction, LeaseExtractionResult, proposal_items
from app.extraction.validation import validate_lease_fields
from app.rules.engine import annotate, evaluate
from app.rules.schemas import RulePolicy, RuleReport
from app.search.router import Citation
from app.workflows.errors import WorkflowFailure
from app.workflows.schemas import (
    CallMetadata,
    Candidate,
    Classification,
    Confidence,
    Extraction,
    VerifiedCandidate,
    WorkflowResult,
)

REVISION = "evidence-agent-v1"
LEASE_REVISION = "lease-field-agent-v1"
RULE_LEASE_REVISION = "lease-field-agent-v2"
STAGES = (
    "load_document",
    "classify_document",
    "retrieve_context",
    "extract_candidates",
    "validate_schema",
    "validate_evidence",
    "calculate_confidence",
    "decision",
)


LEASE_STAGES = (*STAGES[:3], "extract_lease_fields", *STAGES[4:])


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    rule_policy: RulePolicy | None = None
    rule_report: RuleReport | None = None
    extraction_mode: Literal["CANDIDATES", "LEASE_FIELDS"] = "CANDIDATES"
    lease_proposals: LeaseExtraction | None = None
    validated_lease: LeaseExtractionResult | None = None
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    sources: list[Citation] = Field(default_factory=list)
    total_chunk_count: int = 0
    classification: Classification | None = None
    context: list[Citation] = Field(default_factory=list)
    extraction: Extraction | None = None
    validated: list[VerifiedCandidate] = Field(default_factory=list)
    calls: list[CallMetadata] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    result: WorkflowResult | None = None


class Ports(Protocol):
    async def load(self) -> tuple[list[Citation], int]: ...
    async def classify(self, sources: list[Citation]) -> tuple[Classification, CallMetadata]: ...
    async def retrieve(self, kind: str) -> list[Citation]: ...
    async def extract(self, sources: list[Citation]) -> tuple[Extraction, CallMetadata]: ...
    async def extract_lease(
        self, sources: list[Citation]
    ) -> tuple[LeaseExtraction, CallMetadata]: ...
    async def guard(self, sources: list[Citation]) -> None: ...
    async def record(self, stage: str, status: str, calls: list[CallMetadata]) -> None: ...


def validate_candidates(
    candidates: tuple[Candidate, ...], context: list[Citation]
) -> list[VerifiedCandidate]:
    by_id = {source.chunk_id: source for source in context}
    result = []
    seen = set()
    for candidate in candidates:
        source = by_id.get(candidate.chunk_id)
        if source is None or candidate.value not in candidate.quote:
            raise WorkflowFailure("WORKFLOW_EVIDENCE_INVALID")
        position = source.evidence.find(candidate.quote)
        if position < 0 or position != source.evidence.rfind(candidate.quote):
            raise WorkflowFailure("WORKFLOW_EVIDENCE_INVALID")
        identity = (candidate.kind, source.chunk_id, position, candidate.quote)
        if identity in seen:
            raise WorkflowFailure("WORKFLOW_DUPLICATE_CANDIDATE")
        seen.add(identity)
        start = source.start_offset + position
        result.append(
            VerifiedCandidate(
                kind=candidate.kind,
                value=candidate.value,
                citation=source,
                quote_start=start,
                quote_end=start + len(candidate.quote),
            )
        )
    return result


class Node(Protocol):
    def __call__(self, state: AgentState) -> Awaitable[dict[str, object]]: ...


async def execute(initial: AgentState, ports: Ports) -> AgentState:
    async def load(state: AgentState) -> dict[str, object]:
        sources, total = await ports.load()
        if any(
            source.document_id != state.document_id or source.version_id != state.version_id
            for source in sources
        ):
            raise WorkflowFailure("WORKFLOW_EVIDENCE_INVALID")
        return {"sources": sources, "total_chunk_count": total}

    async def classify(state: AgentState) -> dict[str, object]:
        value, metadata = await ports.classify(state.sources)
        return {"classification": value, "calls": [*state.calls, metadata]}

    async def retrieve(state: AgentState) -> dict[str, object]:
        assert state.classification is not None
        context = await ports.retrieve(state.classification.kind)
        if not context:
            raise WorkflowFailure("WORKFLOW_CONTEXT_NOT_FOUND")
        return {"context": context}

    async def extract(state: AgentState) -> dict[str, object]:
        if state.extraction_mode == "LEASE_FIELDS":
            lease, metadata = await ports.extract_lease(state.context)
            return {"lease_proposals": lease, "calls": [*state.calls, metadata]}
        value, metadata = await ports.extract(state.context)
        return {"extraction": value, "calls": [*state.calls, metadata]}

    async def schema(state: AgentState) -> dict[str, object]:
        if state.extraction_mode == "LEASE_FIELDS":
            if state.lease_proposals is None:
                raise WorkflowFailure("WORKFLOW_SCHEMA_INVALID")
            LeaseExtraction.model_validate(state.lease_proposals.model_dump(), strict=True)
            return {}
        if state.extraction is None:
            raise WorkflowFailure("WORKFLOW_SCHEMA_INVALID")
        Extraction.model_validate(state.extraction.model_dump(), strict=True)
        return {}

    async def evidence(state: AgentState) -> dict[str, object]:
        await ports.guard([*state.sources, *state.context])
        if state.extraction_mode == "LEASE_FIELDS":
            assert state.lease_proposals is not None
            return {"validated_lease": validate_lease_fields(state.lease_proposals, state.context)}
        assert state.extraction is not None
        return {"validated": validate_candidates(state.extraction.candidates, state.context)}

    async def rules(state: AgentState) -> dict[str, object]:
        if state.validated_lease is None or state.rule_policy is None:
            raise WorkflowFailure("RULE_INPUT_UNAVAILABLE")
        await ports.guard([*state.sources, *state.context])
        report = evaluate(state.validated_lease, state.rule_policy, utcnow())
        return {"validated_lease": annotate(state.validated_lease, report), "rule_report": report}

    async def confidence(state: AgentState) -> dict[str, object]:
        count = len(state.extraction.candidates) if state.extraction else 0
        validated_count = len(state.validated)
        if state.extraction_mode == "LEASE_FIELDS":
            count = (
                sum(1 for _ in proposal_items(state.lease_proposals))
                if state.lease_proposals
                else 0
            )
            validated_count = len(state.validated_lease.fields) if state.validated_lease else 0
        return {
            "confidence": Confidence(evidence_coverage=validated_count / count if count else None)
        }

    async def decision(state: AgentState) -> dict[str, object]:
        assert state.classification is not None
        await ports.guard([*state.sources, *state.context])
        return {
            "result": WorkflowResult(
                classification=state.classification,
                lease_extraction=state.validated_lease,
                rule_report=state.rule_report,
                scope="LEASE_FIELD_PROPOSALS"
                if state.extraction_mode == "LEASE_FIELDS"
                else "TITLE_AND_HEADING_CANDIDATES_ONLY",
                candidates=state.validated,
                confidence=state.confidence,
                source_chunk_count=len(state.sources),
                total_chunk_count=state.total_chunk_count,
                retrieved_chunk_count=len(state.context),
            )
        }

    def wrap(name: str, node: Callable[[AgentState], Awaitable[dict[str, object]]]) -> Node:
        async def run(state: AgentState) -> dict[str, object]:
            await ports.record(name, "STARTED", state.calls)
            try:
                update = await node(state)
            except Exception:
                await ports.record(name, "FAILED", state.calls)
                raise
            next_state = AgentState.model_validate({**state.model_dump(), **update})
            await ports.record(name, "COMPLETED", next_state.calls)
            return update

        return run

    stages: tuple[str, ...] = LEASE_STAGES if initial.extraction_mode == "LEASE_FIELDS" else STAGES
    nodes: tuple[Callable[[AgentState], Awaitable[dict[str, object]]], ...] = (
        load,
        classify,
        retrieve,
        extract,
        schema,
        evidence,
        confidence,
        decision,
    )
    if initial.extraction_mode == "LEASE_FIELDS" and initial.rule_policy is not None:
        stages = (*stages[:6], "run_rules", *stages[6:])
        nodes = (*nodes[:6], rules, *nodes[6:])
    builder = StateGraph(AgentState)
    for name, node in zip(stages, nodes, strict=True):
        builder.add_node(name, wrap(name, node), retry_policy=())
    builder.add_edge(START, STAGES[0])
    builder.add_edge(STAGES[0], STAGES[1])
    builder.add_conditional_edges(
        "classify_document",
        lambda state: (
            "retrieve_context"
            if state.classification and state.classification.kind in {"LEASE", "AMENDMENT"}
            else "calculate_confidence"
        ),
        {"retrieve_context": "retrieve_context", "calculate_confidence": "calculate_confidence"},
    )
    for a, b in pairwise(stages[2:]):
        builder.add_edge(a, b)
    builder.add_edge("decision", END)
    graph = builder.compile(checkpointer=None, debug=False)
    # LangGraph state includes private source. Never opt it into ambient LangSmith tracing.
    with tracing_context(enabled=False):
        output = await graph.ainvoke(initial, config={"recursion_limit": 16, "callbacks": []})
    return AgentState.model_validate(output)
