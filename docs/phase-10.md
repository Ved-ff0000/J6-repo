# Phase 10 — evidence-backed lease extraction schemas

## Scope and inspection

Reviewed the supplied twelve Phase 10 groups and first-class field-evidence model
against Phase 9's working typed graph, source citations, paid-egress admission,
idempotent SQL ledger and parsed-document UI. The implementation extends that path
rather than replacing working retrieval or silently upgrading old results.

Implemented: twelve closed typed proposal schemas → strict provider schema → exact
field evidence validation → conservative literal normalization → explicit workflow
mode and persisted revision → grouped UI/schema catalog → tests/migration/runbook.

**Every field remains MODEL_PROPOSED_UNVERIFIED and every completed run remains
REVIEW_REQUIRED.** This phase does not approve a lease, run compliance rules,
reconcile amendments, populate an authoritative lease/graph entity or create a
review task. Rules, conflicts and human adjudication remain Phases 11–13. Schema
coverage is not proof of whole-document extraction completeness or legal accuracy.

## Typed groups

`app.extraction.schemas.LeaseExtraction` is version `lease-extraction-v1`:

| Group | Typed field labels |
| --- | --- |
| parties | landlord, tenant, guarantor, agent, other |
| dates | execution, commencement, expiration, rent_commencement, possession, other |
| rent | base_rent, security_deposit, percentage_rent, other |
| renewals | option_count, duration, notice_period, rent_basis, conditions, other |
| termination | right, trigger, notice_period, fee, other |
| notices | period, recipient, address, method, other |
| obligations | responsibility, deadline, payment, other |
| insurance | coverage, limit, certificate, additional_insured, other |
| maintenance | responsibility, repair, replacement, cost, other |
| assignment | consent, restriction, subletting, fee, other |
| cam | charge, allocation, cap, exclusion, reconciliation, other |
| escalation | method, rate, frequency, cap, floor, other |

Groups are sparse arrays, max six proposals per group / 24 total. The strict provider
schema requires **all twelve arrays**, including empty arrays. Each item has:
`field`, exact `value` text (1–300 characters), source chunk UUID, exact supporting
`quote` (1–500), and interpretation EXPLICIT / AMBIGUOUS / CONDITIONAL /
CROSS_REFERENCE. Empty/blank strings, wrong group labels, arbitrary fields, model
normalizations, model confidence and approval decisions are rejected.

An empty group means **NOT_OBSERVED_IN_CONTEXT**, not absent from the lease. A run
classifying the sample OTHER/UNKNOWN skips extraction and stores
`lease_extraction:null`; it does not fabricate twelve empty groups. Multiple
supported proposals can coexist; no canonical winner or amendment/base-lease merge
is selected. AMENDMENT mode extracts only from the selected version's context.

## First-class evidence and normalization

Before publication each proposal must refer to this run's already tenant-scoped
retrieved context. The quote must occur exactly once in the chunk and the value
exactly once within the quote. Missing/forged references, invented text, ambiguous
positions or exact duplicate field proposals fail the **whole** run with no partial
result. Source eligibility/integrity and actor access retain Phase 9's fresh guards.

Each stored field includes raw value, interpretation, quote, exact quote/value
start/end offsets, citation with document/version/page/section/parser provenance,
evidence status EXACT_QUOTE_MATCH, unknown semantic confidence, explicit unverified
status and explanation. Offsets refer to the parser's normalized page text; PDF vs
logical DOCX/TXT page labels are preserved, not invented printed-page numbers.
Provider/model/prompt/version/message hash/usage metadata is retained on the run.
No standalone value is stored without its source evidence.

`literal-normalizer-v1` is a small literal parser, **not the Phase 11 rule engine**:

- Dates normalize only valid explicit `YYYY-MM-DD` calendar dates. Ambiguous locale
  formats, relative dates and invalid dates stay raw and require review.
- Money normalizes only an explicit supported uppercase currency code, bounded
  numeric amount with valid comma grouping, and optional literal `per month/year/
  week/day` or `one-time`. Supported codes: USD/EUR/GBP/INR/AUD/CAD/NZD/SGD/HKD/JPY/
  CNY/CHF/AED/SAR. Currency symbols, unknown codes, malformed grouping, alternative
  amounts, exponents and spelled numbers are not guessed. Decimal strings preserve
  exact precision; no float rounding, exchange rates, annualization or minor-unit
  validation is asserted.
- Literal duration parsing supports numeric days/weeks/months/years on renewal,
  termination and notice period fields; business-day rules, spelled quantities and
  deadline arithmetic are not inferred. Units are not converted.
- Simple numeric percentages normalize for percentage-rent/escalation/CAM-cap
  fields; CPI formulas and compound expressions remain raw. A parsed number does
  not establish an enforceable percentage or rule-compliant value.
- Items **marked by the model** ambiguous/conditional/reference-only are not
  normalized. The model's interpretation can itself be wrong; code does not prove
  conditionality, applicability, party roles or clause meaning from substring checks.

Output `normalization_status` is NORMALIZED, REQUIRES_REVIEW or NOT_APPLICABLE.
Unknown values are null, never invented zeroes. Raw values/evidence always remain.
Per-field `confidence:null` and `rule_validation:{status:NOT_RUN,passed:null,rules:[]}`
are explicit. The graph's exact evidence-coverage ratio remains a quote-match ratio,
not semantic confidence, full-source coverage or accuracy. Document completeness is
always NOT_ESTABLISHED; no authoritative lease record is created.

## Integration, limits and APIs

`WorkflowIn.extraction_mode` selects CANDIDATES (backward-compatible default) or
LEASE_FIELDS. The latter uses graph revision `lease-field-agent-v1` and a genuine
`extract_lease_fields` node. Its separate versioned `workflow_lease_extraction`
prompt (1.0.0, authored 2026-10-16) requests typed fields from untrusted source data;
it cannot execute tools, select arbitrary graph transitions or supply normalized
values. Original candidate prompt/revision semantics are unchanged.

Both modes retain **at most two generation calls**, shared owned Redis admission,
source/actor guards, explicit paid document-egress consent, bounded HTTP output and
no automatic retries/resume. Lease mode uses a fixed lease-topic retrieval query;
only up to three retrieved chunks / 20,000 serialized source characters are sent
to extraction. This is partial-context extraction, not exhaustive analysis of all
pages. Longer leases, missing retrieved clauses, output limits, OCR/layout gaps or
model error can omit fields. No additional per-category paid calls are hidden.

Existing admin-only workflow endpoints continue to run/read/replay records:

- POST `/api/v1/documents/{id}/versions/{n}/workflow-runs` with:
  `{"request_key":"<fresh UUID>","acknowledge_external_request":true,"extraction_mode":"LEASE_FIELDS"}`.
  No caller-supplied lease text, field values, credentials, model or tenant is accepted.
- GET the same path supports optional `extraction_mode=CANDIDATES|LEASE_FIELDS`
  with existing bounded limit/offset history; omitted filter returns both modes.
- GET `/api/v1/workflow-runs/{id}` returns the same scoped run/result.
- GET `/api/v1/lease-extractions/schema` returns the closed provider proposal schema,
  result schema, version and limits; it is admin-only and never calls a model.
- GET `/api/v1/workflows/config` includes the independently gated lease capability,
  schema/revision and mode-specific stages. No endpoint/key is exposed.

An idempotency key is now bound to **organization + document version + mode**.
Reusing it with another mode returns 409, never a new call or relabeled old result.
Recorded results can be replayed after lease generation is disabled, with no model
calls. STARTED stays pending/unknown (202), not auto-resumed. Failures may still bill;
unrecorded token usage is not zero. Existing HTTP status/result semantics from the
Phase 9 runbook remain unchanged.

In Documents → parsed version → Bounded evidence workflow, choose **Lease field
proposals (12 groups)**, then explicitly confirm source egress. The UI displays
all groups, raw values, interpretation, exact evidence/offsets, optional literal
normalization, not-run rules and completeness warnings. Changing the selector
after a lost response does not change the saved retry mode/key. No mutation is
triggered by page load, group expansion, mode selection or history refresh.

## Upgrade and safe configuration

No new service/container or dependency is required beyond the existing backend:

```sh
# Supported environment: Python 3.12, existing dependencies activated.
pip install -e './backend[dev]'
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Separate terminal from repo root:
cd frontend
npm ci
npm run dev -- --host 0.0.0.0
```

Or from root use the existing local stack:
`python scripts/init_dev_env.py && docker compose up --build`.
Readiness now requires schema `phase10`. Migration `0009_lease_extractions` adds a
checked extraction-mode column defaulting existing rows to CANDIDATES, without
rewriting old revision/result JSON. It refuses downgrade while **any LEASE_FIELDS
rows exist**, including unknown/failed runs. Preserve/export and reconcile them
under an approved retention/rollback plan before removal; never silently reinterpret
new records as candidate runs. PostgreSQL offline rollback SQL includes the guard.

Lease generation remains **off** independently of ordinary candidate workflows.
For an approved synthetic trial, configure privately on the server:

```dotenv
LEASEGUARD_RETRIEVAL_ENABLED=true
LEASEGUARD_LLM_ENABLED=true
LEASEGUARD_WORKFLOW_ENABLED=true
LEASEGUARD_LEASE_EXTRACTION_ENABLED=true
LEASEGUARD_LLM_MAX_OUTPUT_TOKENS=2048
# Existing model/HTTPS URL/private key and Phase 9 timeout/admission settings apply.
```

At least 1024 output tokens are required when enabling lease mode; 2048 is the
existing hard ceiling and recommended for broader proposals. No output budget is
silently increased by the request. A truncated response fails closed, without an
automatic paid repair/continuation. No real key is generated, committed or requested
in chat. Existing scan policy still limits unscanned opt-in to synthetic development
and blocks its production bypass. Normal workflows/provider readiness never bill
models automatically. Approved network/privacy/retention policy and independent
upstream spend caps are still necessary; org-unit admission is not account billing.

## Verification and boundaries

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0009_lease_extractions:base --sql
# Real migrated disposable infrastructure; model HTTP still explicitly mocked:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

Sandbox: **196 backend tests passed, 11 opt-in tests skipped; 31 frontend passed**.
Ruff/formatting, mypy (66 modules), TypeScript/build and npm audit pass. SQLite
migration forward/reverse/model matching and a populated legacy-data/rollback-guard
test pass. PostgreSQL forward/reverse SQL renders offline. Tests exercise all twelve
groups in the real local graph/parser/SQLite/Qdrant path with explicitly mocked
model HTTP, including evidence, schema attacks, ambiguity, normalization, empty vs
skipped extraction, mode replay, UI escaping and mode-stable safe retry.

Ten live-infrastructure integration variants (now including both extraction modes)
and the explicitly paid provider test are skipped locally: service binaries and
credentials are absent. The infrastructure variants use real PG/Redis/MinIO/Qdrant
but mock model HTTP; no remote CI result is claimed. No real LLM calls were made
(provider hosts also lie outside sandbox egress policy). Live model accuracy,
whole-lease completeness, full browser end-to-end, real PostgreSQL deployment and
Docker builds remain unverified. This is not production/legal certification.

Stopped after Phase 10. Next only on approval: Phase 11 versioned deterministic rules.
