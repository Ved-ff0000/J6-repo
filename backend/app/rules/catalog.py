"""Immutable code-reviewed rules. There is no upload/eval/SQL/HTTP action language."""

from datetime import date

from app.rules.schemas import RuleDefinition, RulePolicy

SPECS = (
    (
        "LG-001",
        "Source linkage",
        "Quote and value offsets must exactly match the stored citation. This does not prove field meaning.",
        "CRITICAL",
        "EXACT_SOURCE",
        "FIELD",
    ),
    (
        "LG-002",
        "Explicit interpretation",
        "Ambiguous, conditional and cross-reference proposals require review.",
        "WARNING",
        "EXPLICIT_INTERPRETATION",
        "FIELD",
    ),
    (
        "LG-003",
        "Literal normalization consistency",
        "Recompute the versioned literal parser and compare normalized representations; no inferred units or currencies.",
        "ERROR",
        "LITERAL_CONSISTENCY",
        "FIELD",
    ),
    (
        "LG-004",
        "Positive duration",
        "Explicit supported renewal/notice duration proposals must be greater than zero; this is not a legal notice minimum.",
        "ERROR",
        "POSITIVE_DURATION",
        "FIELD",
    ),
    (
        "LG-005",
        "Money scale review",
        "Review money amounts exceeding the reference minor-unit scale (JPY zero, other supported currencies two). No rounding or legal invalidity is inferred.",
        "WARNING",
        "MONEY_SCALE",
        "FIELD",
    ),
    (
        "LG-006",
        "Proposed date order",
        "Compare exactly one explicit normalized commencement and expiration from one version. Multiple/missing candidates are indeterminate, not reconciled.",
        "ERROR",
        "DATE_ORDER",
        "EXTRACTION",
    ),
    (
        "LG-007",
        "Termination notice observation",
        "When a termination right/trigger is proposed, check observed termination notice durations. Unobserved periods do not prove absence in the lease.",
        "WARNING",
        "TERMINATION_NOTICE",
        "EXTRACTION",
    ),
    (
        "LG-008",
        "Renewal duration linkage",
        "Check a positive literal duration and a textual renew/renewal marker in its exact quote. Not proof of an enforceable renewal option.",
        "WARNING",
        "RENEWAL_LINK",
        "FIELD",
    ),
)
RULE_IDS = tuple(item[0] for item in SPECS)


def policy(enabled: bool = True, disabled: tuple[str, ...] = ()) -> RulePolicy:
    if len(set(disabled)) != len(disabled) or any(item not in RULE_IDS for item in disabled):
        raise ValueError("Unknown or duplicate disabled rule")
    rules = tuple(
        RuleDefinition.model_validate(
            {
                "id": key,
                "name": name,
                "description": description,
                "version": "1.0.0",
                "severity": severity,
                "enabled": enabled and key not in disabled,
                "conditions": {"operation": operation, "scope": scope},
                "created_at": date(2026, 10, 16),
                "updated_at": date(2026, 10, 16),
            }
        )
        for key, name, description, severity, operation, scope in SPECS
    )
    return RulePolicy(enabled=enabled, rules=rules)
