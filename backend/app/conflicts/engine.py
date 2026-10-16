"""Pure deterministic comparison of partial unverified proposals. No authority inference."""

from datetime import datetime

from app.conflicts.schemas import ComparisonFinding, ComparisonReport, Source
from app.extraction.schemas import LeaseExtractionResult, LeaseField
from app.rules.engine import exact, expected, input_hash, numeric


def compare_field(left: LeaseField, right: LeaseField) -> tuple[str, str]:
    if not exact(left) or not exact(right):
        return "INDETERMINATE", "SOURCE_LINK_NOT_ESTABLISHED"
    if left.interpretation != "EXPLICIT" or right.interpretation != "EXPLICIT":
        return "INDETERMINATE", "INTERPRETATION_REQUIRES_REVIEW"
    a, sa = expected(left)
    b, sb = expected(right)
    if (
        a != left.normalized
        or b != right.normalized
        or sa != left.normalization_status
        or sb != right.normalization_status
    ):
        return "INDETERMINATE", "NORMALIZATION_MISMATCH"
    if left.value == right.value:
        return "MATCH", "IDENTICAL_LITERAL_PROPOSALS_ONLY"
    if a is not None and b is not None:
        ad = a.model_dump()
        bd = b.model_dump()
        if a.kind != b.kind or any(ad.get(k) != bd.get(k) for k in ("currency", "period", "unit")):
            return "INDETERMINATE", "UNITS_CURRENCY_OR_PERIOD_NOT_ALIGNED"
        for key in ("amount", "percent"):
            if key in ad:
                av = numeric(ad[key])
                bv = numeric(bd[key])
                if av is None or bv is None:
                    return "INDETERMINATE", "NUMERIC_REPRESENTATION_UNRESOLVED"
                ad[key] = av
                bd[key] = bv
        return (
            ("MATCH", "EQUAL_NORMALIZED_LITERALS_ONLY")
            if ad == bd
            else ("POTENTIAL_CONFLICT", "DIFFERENT_NORMALIZED_PROPOSALS")
        )
    if a is None and b is None and sa == sb == "NOT_APPLICABLE":
        return "POTENTIAL_CONFLICT", "DIFFERENT_TEXT_PROPOSALS_NOT_SEMANTIC_VERIFICATION"
    return "INDETERMINATE", "UNRESOLVED_LITERAL_FORMAT"


def compare(
    left: LeaseExtractionResult,
    right: LeaseExtractionResult,
    left_source: Source,
    right_source: Source,
    evaluated_at: datetime,
) -> ComparisonReport:
    if (
        left_source.run_id == right_source.run_id
        or left_source.version_id == right_source.version_id
    ):
        raise ValueError("Distinct source versions required")
    for extraction, source in ((left, left_source), (right, right_source)):
        if any(
            f.citation.document_id != source.document_id
            or f.citation.version_id != source.version_id
            or f.citation.version_number != source.version_number
            for f in extraction.fields
        ):
            raise ValueError("Source contract mismatch")
        if any(
            len(f.value) > 300 or len(f.evidence_text) > 500 or len(f.citation.evidence) > 8000
            for f in extraction.fields
        ):
            raise ValueError("Source bounds exceeded")
    latest = None
    if left_source.document_id == right_source.document_id:
        if left_source.version_number == right_source.version_number:
            raise ValueError("Distinct version numbers required")
        relation = "DOCUMENT_VERSIONS"
        latest = max((left_source, right_source), key=lambda s: s.version_number).version_id
    elif (
        right_source.parent_document_id == left_source.document_id
        and right_source.document_type == "AMENDMENT"
    ) or (
        left_source.parent_document_id == right_source.document_id
        and left_source.document_type == "AMENDMENT"
    ):
        relation = "LINKED_AMENDMENT"
    else:
        raise ValueError("Related versions or directly linked amendment required")
    keys = sorted({(f.group, f.field) for e in (left, right) for f in e.fields})
    findings = []
    for group, field in keys:
        a = tuple(f for f in left.fields if (f.group, f.field) == (group, field))
        b = tuple(f for f in right.fields if (f.group, f.field) == (group, field))
        if not a or not b:
            outcome, reason = "NOT_OBSERVED", "UNOBSERVED_IN_PARTIAL_CONTEXT_NOT_ADDED_OR_DELETED"
        elif len(a) != 1 or len(b) != 1:
            outcome, reason = "INDETERMINATE", "MULTIPLE_PROPOSALS_NO_UNIQUE_CLAUSE_ALIGNMENT"
        else:
            outcome, reason = compare_field(a[0], b[0])
        findings.append(
            ComparisonFinding.model_validate(
                {
                    "group": group,
                    "field": field,
                    "outcome": outcome,
                    "reason": reason,
                    "left": a,
                    "right": b,
                }
            )
        )
    outcomes = {f.outcome for f in findings}
    overall = (
        "POTENTIAL_CONFLICTS_DETECTED"
        if "POTENTIAL_CONFLICT" in outcomes
        else "NO_DIFFERENCES_OBSERVED"
        if outcomes == {"MATCH"}
        else "INDETERMINATE"
    )
    return ComparisonReport.model_validate(
        {
            "relationship": relation,
            "left_source": left_source,
            "right_source": right_source,
            "left_input_sha256": input_hash(left),
            "right_input_sha256": input_hash(right),
            "evaluated_at": evaluated_at,
            "findings": tuple(findings),
            "outcome": overall,
            "latest_uploaded_version_id": latest,
        }
    )
