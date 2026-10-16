"""Pure, finite deterministic evaluation. No network, DB, clock reads, dynamic code or mutation."""

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.extraction.normalization import DURATION_FIELDS, MONEY_FIELDS, normalize
from app.extraction.schemas import (
    LeaseExtractionResult,
    LeaseField,
    NormalizedDate,
    NormalizedDuration,
    NormalizedMoney,
    NormalizedValue,
    Proposal,
    RuleValidation,
)
from app.rules.schemas import FieldOutcome, Finding, Outcome, RuleDefinition, RulePolicy, RuleReport


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def input_hash(lease: LeaseExtractionResult) -> str:
    data = lease.model_dump(mode="json")
    for field in data["fields"]:
        field.pop("rule_validation", None)
    return fingerprint(data)


def exact(field: LeaseField) -> bool:
    source = field.citation.evidence
    start = field.quote_start - field.citation.start_offset
    end = field.quote_end - field.citation.start_offset
    offset = field.value_start - field.quote_start
    value_end = field.value_end - field.quote_start
    return (
        0 <= start < end <= len(source)
        and source[start:end] == field.evidence_text
        and source.find(field.evidence_text) == start
        and source.rfind(field.evidence_text) == start
        and 0 <= offset < value_end <= len(field.evidence_text)
        and field.evidence_text[offset:value_end] == field.value
        and field.evidence_text.find(field.value) == offset
        and field.evidence_text.rfind(field.value) == offset
        and field.quote_end - field.quote_start == len(field.evidence_text)
        and field.value_end - field.value_start == len(field.value)
    )


def expected(field: LeaseField) -> tuple[NormalizedValue | None, str]:
    return normalize(
        field.group,
        Proposal(
            field=field.field,
            value=field.value,
            quote=field.evidence_text,
            chunk_id=field.citation.chunk_id,
            interpretation=field.interpretation,
        ),
    )


def numeric(value: str) -> Decimal | None:
    if len(value) > 40 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def field_check(rule: RuleDefinition, field: LeaseField) -> tuple[Outcome, str]:
    operation = rule.conditions.operation
    if operation == "EXACT_SOURCE":
        return ("PASS", "EXACT_SOURCE_LINK") if exact(field) else ("FAIL", "SOURCE_LINK_MISMATCH")
    if operation == "EXPLICIT_INTERPRETATION":
        return (
            ("PASS", "MODEL_MARKED_EXPLICIT_NOT_VERIFIED")
            if field.interpretation == "EXPLICIT"
            else ("INDETERMINATE", "INTERPRETATION_REQUIRES_REVIEW")
        )
    if operation == "POSITIVE_DURATION" and (field.group, field.field) not in DURATION_FIELDS:
        return "NOT_APPLICABLE", "NOT_DURATION_FIELD"
    if operation == "RENEWAL_LINK" and (field.group, field.field) != ("renewals", "duration"):
        return "NOT_APPLICABLE", "NOT_RENEWAL_DURATION"
    if operation == "MONEY_SCALE" and (field.group, field.field) not in MONEY_FIELDS:
        return "NOT_APPLICABLE", "NOT_MONEY_FIELD"
    if not exact(field):
        return "INDETERMINATE", "SOURCE_NOT_ESTABLISHED"
    if field.interpretation != "EXPLICIT":
        return "INDETERMINATE", "INTERPRETATION_REQUIRES_REVIEW"
    normalized, status = expected(field)
    if operation == "LITERAL_CONSISTENCY":
        actual = field.normalized.model_dump(mode="json") if field.normalized else None
        recomputed = normalized.model_dump(mode="json") if normalized else None
        if field.normalization_revision != "literal-normalizer-v1":
            return "INDETERMINATE", "UNSUPPORTED_NORMALIZER"
        if actual != recomputed or field.normalization_status != status:
            return "FAIL", "NORMALIZATION_MISMATCH"
        if status == "NOT_APPLICABLE":
            return "NOT_APPLICABLE", "NO_LITERAL_NORMALIZATION_DEFINED"
        return (
            ("PASS", "LITERAL_REPRESENTATION_MATCHES")
            if normalized
            else ("INDETERMINATE", "LITERAL_FORMAT_UNRESOLVED")
        )
    if operation == "POSITIVE_DURATION":
        if (field.group, field.field) not in DURATION_FIELDS:
            return "NOT_APPLICABLE", "NOT_DURATION_FIELD"
        if not isinstance(normalized, NormalizedDuration):
            return "INDETERMINATE", "DURATION_NOT_NORMALIZED"
        amount = numeric(normalized.amount)
        if amount is None:
            return "INDETERMINATE", "DURATION_NOT_NORMALIZED"
        return (
            ("PASS", "POSITIVE_LITERAL_DURATION")
            if amount > 0
            else ("FAIL", "NONPOSITIVE_LITERAL_DURATION")
        )
    if operation == "MONEY_SCALE":
        if not isinstance(normalized, NormalizedMoney):
            return "INDETERMINATE", "MONEY_NOT_NORMALIZED"
        amount = numeric(normalized.amount)
        if amount is None:
            return "INDETERMINATE", "MONEY_NOT_NORMALIZED"
        scale = 0 if normalized.currency == "JPY" else 2
        fraction = normalized.amount.partition(".")[2].rstrip("0")
        return (
            ("PASS", "REFERENCE_MONEY_SCALE_MATCHES")
            if len(fraction) <= scale
            else ("FAIL", "REVIEW_MONEY_ROUNDING_TERMS")
        )
    if operation == "RENEWAL_LINK":
        if (field.group, field.field) != ("renewals", "duration"):
            return "NOT_APPLICABLE", "NOT_RENEWAL_DURATION"
        if not isinstance(normalized, NormalizedDuration) or numeric(normalized.amount) is None:
            return "INDETERMINATE", "RENEWAL_DURATION_UNRESOLVED"
        amount = numeric(normalized.amount)
        assert amount is not None
        if amount <= 0:
            return "FAIL", "NONPOSITIVE_RENEWAL_DURATION"
        return (
            ("PASS", "RENEWAL_MARKER_AND_LITERAL_ONLY")
            if re.search(r"\brenew(?:al)?\b", field.evidence_text, re.IGNORECASE)
            else ("INDETERMINATE", "RENEWAL_MARKER_NOT_OBSERVED")
        )
    raise ValueError("Unsupported field operation")


def aggregate(outcomes: list[str]) -> str:
    if "FAIL" in outcomes:
        return "FAIL"
    if "INDETERMINATE" in outcomes:
        return "INDETERMINATE"
    if "PASS" in outcomes:
        return "PASS"
    return "DISABLED"


def evaluate(
    lease: LeaseExtractionResult, policy: RulePolicy, evaluated_at: datetime
) -> RuleReport:
    from app.rules.catalog import policy as canonical_policy

    canonical = canonical_policy(
        policy.enabled, tuple(rule.id for rule in policy.rules if not rule.enabled)
    )
    if canonical.model_dump() != policy.model_dump():
        raise ValueError("Unsupported rule policy snapshot")
    if any(
        len(field.value) > 300
        or len(field.evidence_text) > 500
        or len(field.citation.evidence) > 8000
        for field in lease.fields
    ):
        raise ValueError("Rule input exceeds source bounds")
    findings = []

    def add(rule: RuleDefinition, indices: tuple[int, ...], outcome: Outcome, reason: str) -> None:
        findings.append(
            Finding(
                rule_id=rule.id,
                rule_version=rule.version,
                severity=rule.severity,
                outcome=outcome,
                reason=reason,
                field_indices=indices,
                chunk_ids=tuple(
                    dict.fromkeys(lease.fields[index].citation.chunk_id for index in indices)
                ),
            )
        )

    for rule in policy.rules:
        if not policy.enabled or not rule.enabled:
            add(rule, (), "DISABLED", "RULE_DISABLED")
            continue
        if rule.conditions.scope == "FIELD":
            for index, field in enumerate(lease.fields):
                outcome, reason = field_check(rule, field)
                add(rule, (index,), outcome, reason)
            continue
        if rule.conditions.operation == "DATE_ORDER":
            starts = [
                i
                for i, f in enumerate(lease.fields)
                if (f.group, f.field) == ("dates", "commencement")
            ]
            ends = [
                i
                for i, f in enumerate(lease.fields)
                if (f.group, f.field) == ("dates", "expiration")
            ]
            indices = tuple(starts + ends)
            if len(starts) != 1 or len(ends) != 1:
                add(rule, indices, "INDETERMINATE", "DATE_PAIR_MISSING_OR_MULTIPLE")
                continue
            a, b = (lease.fields[i] for i in (starts[0], ends[0]))
            na, _ = expected(a)
            nb, _ = expected(b)
            if (
                not exact(a)
                or not exact(b)
                or a.interpretation != "EXPLICIT"
                or b.interpretation != "EXPLICIT"
                or not isinstance(na, NormalizedDate)
                or not isinstance(nb, NormalizedDate)
                or a.citation.version_id != b.citation.version_id
                or a.citation.document_id != b.citation.document_id
            ):
                add(rule, indices, "INDETERMINATE", "DATE_PAIR_NOT_ESTABLISHED")
                continue
            add(
                rule,
                indices,
                "PASS"
                if date.fromisoformat(na.iso_date) <= date.fromisoformat(nb.iso_date)
                else "FAIL",
                "PROPOSED_DATE_ORDER_CHECK",
            )
        elif rule.conditions.operation == "TERMINATION_NOTICE":
            triggers = [
                i
                for i, f in enumerate(lease.fields)
                if f.group == "termination" and f.field in {"right", "trigger"}
            ]
            notices = [
                i
                for i, f in enumerate(lease.fields)
                if (f.group, f.field) == ("termination", "notice_period")
            ]
            indices = tuple(triggers + notices)
            if not triggers:
                add(rule, indices, "NOT_APPLICABLE", "NO_TERMINATION_TRIGGER_PROPOSED")
                continue
            if any(
                not exact(lease.fields[i]) or lease.fields[i].interpretation != "EXPLICIT"
                for i in triggers
            ):
                add(rule, indices, "INDETERMINATE", "TERMINATION_TRIGGER_UNRESOLVED")
                continue
            if not notices:
                add(rule, indices, "INDETERMINATE", "TERMINATION_NOTICE_NOT_OBSERVED")
                continue
            checks = [
                field_check(
                    RuleDefinition.model_validate(
                        {
                            **rule.model_dump(),
                            "conditions": {"scope": "FIELD", "operation": "POSITIVE_DURATION"},
                        }
                    ),
                    lease.fields[index],
                )[0]
                for index in notices
            ]
            add(
                rule,
                indices,
                "FAIL"
                if "FAIL" in checks
                else "PASS"
                if all(item == "PASS" for item in checks)
                else "INDETERMINATE",
                "OBSERVED_TERMINATION_NOTICE_CHECK",
            )
        else:
            raise ValueError("Unsupported extraction operation")
    field_outcomes = []
    for index, _field in enumerate(lease.fields):
        applicable = [
            f
            for f in findings
            if index in f.field_indices and f.outcome not in {"NOT_APPLICABLE", "DISABLED"}
        ]
        state = aggregate([f.outcome for f in applicable])
        field_outcomes.append(
            FieldOutcome.model_validate(
                {
                    "index": index,
                    "status": state,
                    "passed": True if state == "PASS" else False if state == "FAIL" else None,
                    "rules": tuple(
                        dict.fromkeys(f.rule_id + "@" + f.rule_version for f in applicable)
                    ),
                }
            )
        )
    state = (
        aggregate([f.outcome for f in findings])
        if lease.fields
        else "INDETERMINATE"
        if policy.enabled and any(r.enabled for r in policy.rules)
        else "DISABLED"
    )
    return RuleReport.model_validate(
        {
            "policy": policy,
            "policy_sha256": fingerprint(policy.model_dump(mode="json")),
            "input_sha256": input_hash(lease),
            "outcome": state,
            "findings": tuple(findings),
            "field_outcomes": tuple(field_outcomes),
            "evaluated_at": evaluated_at,
        }
    )


def annotate(lease: LeaseExtractionResult, report: RuleReport) -> LeaseExtractionResult:
    if report.input_sha256 != input_hash(lease):
        raise ValueError("Rule input fingerprint mismatch")
    if tuple(item.index for item in report.field_outcomes) != tuple(range(len(lease.fields))):
        raise ValueError("Rule field indices mismatch")
    fields = [
        field.model_copy(
            update={
                "rule_validation": RuleValidation.model_validate(
                    {"status": outcome.status, "passed": outcome.passed, "rules": outcome.rules}
                )
            }
        )
        for field, outcome in zip(lease.fields, report.field_outcomes, strict=True)
    ]
    return lease.model_copy(update={"fields": fields})
