> Historical phase report. Subsequent phases were authorized and implemented; see [current status](completion-status.md).

# Phase 11 — versioned deterministic rules

## Scope and safety boundary

Eight code-reviewed rules now evaluate the immutable **partial-context, model-proposed,
unverified** lease fields from Phase 10. These checks are not legal compliance,
semantic verification, completeness assessment, canonical lease updates or approval.
All outcomes retain `decision: REVIEW_REQUIRED`, `legal_compliance_established:false`,
null semantic confidence and the original proposal status. No conflicts, review-task
creation or immutable audit service is implemented here (Phases 12–14 remain future).

An unobserved termination notice is **INDETERMINATE**, not proof the lease has no
notice clause. This deliberately does not implement the spec's illustrative
missing-null → FAIL example against incomplete model/retrieval context. A supported
literal zero duration can fail; an unresolved business-day quantity cannot establish
that failure. Even all-pass reports do not validate the lease or its legal meaning.

## Catalog and execution

`backend/app/rules/` contains closed schemas, immutable catalog, pure engine, SQL
ledger and authenticated API. Every definition includes id, name, description,
version, severity, enabled state, typed condition operation/scope, annotation/review
intent actions, and created/updated dates. Severity vocabulary is INFO, WARNING,
ERROR, CRITICAL; the initial catalog uses WARNING/ERROR/CRITICAL. All initial rules
are version 1.0.0; engine revision is `deterministic-rules-v1`.

| ID | Severity | Deterministic check / limits |
| --- | --- | --- |
| LG-001 | CRITICAL | Exact, unique quote/value linkage and offsets against the stored citation; not semantic role correctness. |
| LG-002 | WARNING | Model marked interpretation EXPLICIT; ambiguous/conditional/cross-reference proposals remain indeterminate. An explicit label is not independently verified. |
| LG-003 | ERROR | Recompute literal-normalizer-v1; mismatch fails, unsupported or ambiguous representations are indeterminate. |
| LG-004 | ERROR | Supported explicit renewal/notice durations must be positive; no legal threshold or day-count interpretation. |
| LG-005 | WARNING | Reference money precision (JPY zero, other supported currencies two decimal places); extra nonzero fraction fails this review check, not a declaration of legal invalidity. No rounding. |
| LG-006 | ERROR | Exactly one explicit normalized commencement/expiration from one version; inverted order fails. Missing/multiple/ambiguous inputs are indeterminate, never reconciled. |
| LG-007 | WARNING | For a proposed termination right/trigger, inspect observed notice durations. Unobserved notice, ambiguous trigger or unresolved duration is indeterminate; zero literal duration fails. |
| LG-008 | WARNING | Positive literal renewal duration plus textual renew/renewal marker in its exact quote; not an enforceable renewal-clause finding. Missing marker is indeterminate. |

There is no rule upload, expression evaluator, arbitrary code/SQL, external action,
model, DB, network or wall-clock read inside `evaluate`. Its caller supplies time.
Input is bounded by the Phase 10 limits (24 fields, bounded citation/value lengths);
findings are bounded at 200. Results include PASS, FAIL, INDETERMINATE,
NOT_APPLICABLE and DISABLED. Overall aggregation prioritizes FAIL, then
INDETERMINATE, then PASS; no evaluated applicable findings means DISABLED. Empty
extraction with active rules is indeterminate. NOT_APPLICABLE is never a field pass.

Each report pins the **full enabled-rule policy**, engine/rule versions, canonical
JSON SHA-256 policy/input fingerprints, timestamp, findings, source-field indices,
chunk IDs and derived per-field rule flags. The input hash excludes derived
`rule_validation` flags so annotation does not change the evaluated input identity.
Input and original extraction objects are not mutated. Unsupported/forged current
policy versions are rejected rather than silently running changed code. Historical
reports are stored/read as snapshots; this phase does not ship a replay interpreter
for hypothetical future engine revisions. Versions must change when semantics do.

## Workflow and local re-evaluation

With rules and LEASE_FIELDS enabled, new workflow runs use `lease-field-agent-v2`
and insert `run_rules` after evidence validation. The enabled policy is captured for
the graph; the report and copied per-field flags are stored in its result. It still
uses only the existing two model generations. Candidate mode and rules-disabled
lease mode retain their old revision/graph. Old request-key replay never upgrades a
historical result or incurs fresh model calls.

Admin-only (`ai:manage`) API:

- `GET /api/v1/rules/catalog`: current immutable definitions and execution setting.
- `POST /api/v1/workflow-runs/{id}/rule-evaluations`: strict JSON
  `{"request_key":"<client-generated UUID>"}` only; makes no external calls.
- `GET /api/v1/workflow-runs/{id}/rule-evaluations?limit=20&offset=0`:
  tenant-scoped history, maximum limit 50.
- `GET /api/v1/rule-evaluations/{id}`: source-visible historical snapshot.

Local evaluation can use an old completed Phase 10 LEASE_FIELDS extraction even
when LLM/retrieval/workflow execution is disabled. It requires current rules enabled,
a REVIEW_REQUIRED lease result, latest PARSED nonarchived version/checksum, and
fresh SQL citations matching its recorded citations. Changed source contracts reject
with 409; hidden/archived/other-tenant parent sources are not exposed. Current admin,
organization and unrevoked/unexpired session are rechecked before publication. SQL
source/authorization locks and composite FKs protect scope; the request-key unique
constraint handles concurrent insert conflicts by replaying the matching record or
returning 409. Real PostgreSQL concurrency remains an infrastructure validation gate,
not a demonstrated local result.

Records are append-only **through this API** (no update/delete routes), not a
database-enforced immutable audit trail against privileged SQL access. A new UUID
creates another record with the current policy; the same organization/request UUID
replays its stored snapshot, even if execution has since been disabled. Reuse for a
different run is 409. SQL-only retries cannot spend provider budget. Original workflow
JSON is never rewritten. Existing source visibility still governs historical reads.

The Documents workflow panel renders inline snapshots and up to the latest ten
separate evaluations, including versions, severity, reason codes, field links and
fingerprints. It offers explicit local evaluation, same-key retry and history refresh.
No evaluation POST happens automatically. Full pagination is available via the API.
New evaluation is disabled in the UI while server rules are off; old results remain
readable. Missing/error responses are not represented as a pass.

## Configuration and operation

Default is off; CI also defaults off and uses explicit model mocks for workflow tests.
For existing stored eligible extractions, only the rules flag is needed:

```sh
LEASEGUARD_RULES_ENABLED=true
LEASEGUARD_RULES_DISABLED=[]
# Example JSON environment value to disable one known rule:
# LEASEGUARD_RULES_DISABLED='["LG-005"]'
```

Unknown or duplicate IDs reject configuration. There are no mutable tenant policies
or user-defined rule conditions. Restart backend/worker after changing configuration.
To annotate NEW lease extractions, also satisfy all Phase 10 LLM/retrieval/workflow
configuration, explicit external-call consent, model token limit and source-index
requirements in [Phase 10](phase-10.md). Turning rules on alone never enables paid AI.

Run from the repository with the development virtualenv activated:

```sh
(cd backend && python -m alembic upgrade head)
./scripts/check.sh
(cd frontend && npm audit --audit-level=high)
```

Migration `0010_rules` adds the source-scoped append-only evaluation ledger and
unique workflow/source parent index; readiness baseline becomes phase11. It preserves
old workflow JSON/modes/revisions. Downgrade refuses if evaluation records OR
`lease-field-agent-v2` runs exist; export/preserve and explicitly reconcile those
records under a reviewed maintenance plan before rollback. Do not delete history
merely to bypass this guard. With no such records, downgrade drops only the new
schema/index and restores phase10; Phase 10's own lease-data rollback guard remains.
PostgreSQL offline SQL includes an executable guard, but SQL rendering is not execution.

## Verification and remaining limits

Unit/SQLite HTTP tests exercise deterministic repeatability, hashes, no mutation,
exact source linkage, normalization mismatch, duration/money/date/notice/renewal
boundaries, disabled/unknown policies, version rejection, no extra graph/model calls,
local no-network evaluation, immutable history, replay/key conflicts, tenant/RBAC,
changed/archived sources, fresh authorization and composite database parent guards.
Migration tests cover legacy preservation, schema/model parity, safe roundtrip and
refusal to erase rules-enabled runs/evaluations. Frontend tests cover escaped output,
nonapproval labels, disabled/readable history, explicit local evaluation, same-key
retries and visible failures. Opt-in infrastructure workflow tests include rule-enabled
lease mode and local evaluation with mock provider HTTP, plus scoped cleanup.

No real legal dataset, model accuracy assessment, paid provider call, real PostgreSQL
concurrency execution, or local multi-service deployment is claimed. See the final
phase report for exact check counts and skips. Use synthetic documents only.

**Stopped after Phase 11. Next, only on authorization: Phase 12 — conflict detection.**

### Local verification — 2026-10-16

- Backend: **223 passed, 11 skipped** (unavailable infrastructure variants and the
  opt-in paid-provider test); Ruff checks/format and mypy **72 source files** clean.
- Frontend: **35 passed**; Prettier, TypeScript and production build pass;
  `npm audit --audit-level=high` reports **0 vulnerabilities**.
- SQLite migration/model parity, populated legacy preservation, rollback guards and
  database-parent rejection tests pass. PostgreSQL forward/reverse SQL renders;
  this is not a real PostgreSQL migration run.
- Preview OpenAPI exposes the rule contracts. Four anonymous rule routes return
  401. Frontend Documents shell and same-origin health proxy return 200;
  proxied rule catalog returns 401. Liveness is 200, readiness correctly 503/not_ready.
- Attempting the preview's real `alembic upgrade head` could not connect to PostgreSQL
  on port 5432. No PostgreSQL migration was applied. Provision dependencies and run
  that command before using authenticated data workflows. API preview was refreshed
  for route/auth smoke only; no data-ready deployment or paid model result is claimed.
