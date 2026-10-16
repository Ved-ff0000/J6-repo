"""Require exact evidence in the run's already scoped context before deriving any normalized value."""

from app.extraction.normalization import normalize
from app.extraction.schemas import (
    GROUPS,
    GroupSummary,
    LeaseExtraction,
    LeaseExtractionResult,
    LeaseField,
    proposal_items,
)
from app.search.router import Citation
from app.workflows.errors import WorkflowFailure


def validate_lease_fields(
    proposals: LeaseExtraction, context: list[Citation]
) -> LeaseExtractionResult:
    sources = {item.chunk_id: item for item in context}
    fields = []
    seen = set()
    counts = {group: 0 for group in GROUPS}
    for group, proposal in proposal_items(proposals):
        source = sources.get(proposal.chunk_id)
        if source is None:
            raise WorkflowFailure("LEASE_EVIDENCE_INVALID")
        position = source.evidence.find(proposal.quote)
        value_position = proposal.quote.find(proposal.value)
        if (
            position < 0
            or position != source.evidence.rfind(proposal.quote)
            or value_position < 0
            or value_position != proposal.quote.rfind(proposal.value)
        ):
            raise WorkflowFailure("LEASE_EVIDENCE_INVALID")
        identity = (
            group,
            proposal.field,
            source.chunk_id,
            position,
            proposal.quote,
            proposal.value,
        )
        if identity in seen:
            raise WorkflowFailure("LEASE_DUPLICATE_FIELD")
        seen.add(identity)
        quote_start = source.start_offset + position
        value_start = quote_start + value_position
        normalized, status = normalize(group, proposal)
        fields.append(
            LeaseField(
                group=group,
                field=proposal.field,
                value=proposal.value,
                interpretation=proposal.interpretation,
                normalized=normalized,
                normalization_status=status,
                citation=source,
                evidence_text=proposal.quote,
                quote_start=quote_start,
                quote_end=quote_start + len(proposal.quote),
                value_start=value_start,
                value_end=value_start + len(proposal.value),
            )
        )
        counts[group] += 1
    return LeaseExtractionResult(
        fields=fields,
        groups=[
            GroupSummary.model_validate(
                {
                    "group": group,
                    "proposed_count": counts[group],
                    "observation": "PROPOSED_VALUES"
                    if counts[group]
                    else "NOT_OBSERVED_IN_CONTEXT",
                }
            )
            for group in GROUPS
        ],
    )
