> Historical phase report. Subsequent phases were authorized and implemented; see [current status](completion-status.md).

# Phase 12 — amendment / version conflict comparison

## Delivered scope

Administrators can explicitly compare two stored, source-backed LEASE_FIELDS runs,
without new model, vector, storage-service or other external calls. Supported pairs:

1. **Two distinct uploaded versions of the same document**, including historical
   parsed versions; neither must be the currently uploaded version.
2. **A document and its directly linked amendment**, using the existing
   `parent_document_id` relationship and user-supplied `document_type: AMENDMENT`.
   Orientation can be reversed. No relationship is inferred from similar filenames,
   property labels, model classification or retrieval similarity.

Example: one source proposes renewal **3 years**, the other **5 years**. The report
flags `POTENTIAL_CONFLICT`, retaining both raw values, literal normalized forms,
quotes, offsets, citations and workflow/source identities. **Neither is overwritten.**
Previous workflow results, rules, original versions and historical records remain
unchanged. This is a proposal comparison, not a canonical lease reconciliation.

### Authority is deliberately not inferred

The spec's “latest authoritative document” cannot be established from our current
partial, unverified proposals, upload timestamps, document type or parent linkage.
Reports explicitly retain `authority_status: NOT_ESTABLISHED` and
`decision: REVIEW_REQUIRED`. For versions of one document, the higher version number
is identified as `latest_uploaded_version_id` **among the two compared sources only**;
this is not legal authority, effective date, execution status or supersession. For a
linked amendment, no cross-document latest/authority winner is assigned.

No execution/effective dates, signatures, amendment chains, supersedes/superseded-by
legal relationships, or reviewer authority decisions are invented. A full legal
precedence resolver and whole-family timeline remain beyond these partial-input
checks. Phase 13 human review is not implemented here. No automatic approval,
conflict resolution, review task, canonical value replacement or audit service is
created by comparison.

## Pure comparison engine

`backend/app/conflicts/engine.py` is deterministic, bounded and has no IO/clock read;
time is supplied by its caller. Revision: `proposal-comparison-v1`.

- Fields align only on the Phase 10 `(group, field)` key. More than one candidate
  on either side is **INDETERMINATE**, not an arbitrary clause pairing.
- Missing from either partial context is **NOT_OBSERVED**, never “deleted”, “added”,
  or proven absent. Empty comparisons are indeterminate.
- Source quote/value linkage is recomputed. Nonexact linkage or nonexplicit model
  interpretation is indeterminate. Model labels/field roles remain unverified.
- Literal normalization is recomputed using `literal-normalizer-v1`; inconsistent
  stored representations are indeterminate, not trusted comparisons.
- Identical supported/exact explicit raw text is a literal MATCH only. Otherwise,
  normalized dates, durations, money and percentages compare without rounding.
  Decimal equality ignores insignificant trailing zeroes using bounded Decimal
  values, never binary floats. Currency, period and duration unit must match.
- No currency exchange, 36 months → 3 years conversion, business-day interpretation,
  semantic text equivalence, thresholds, legal applicability or clause/entity
  reconciliation is attempted. Unsupported formats remain indeterminate.
- Different explicit comparable normalized values, or different explicit raw text
  in fields without a defined literal normalizer, are **POTENTIAL_CONFLICT** only.
  Different text is not proof of different legal meaning.

Overall outcome is POTENTIAL_CONFLICTS_DETECTED if any potential conflict exists;
NO_DIFFERENCES_OBSERVED only when all observed field-key findings are MATCH;
otherwise INDETERMINATE. Even a match-only report does not establish document
agreement or completeness. Reports always retain `completeness_established:false`
and `previous_values_overwritten:false`.

At most 24 input fields per side and 48 field-key findings are allowed, with the
existing source text bounds. Each report pins source/version/parent/type snapshots,
engine revision, evaluation time and both input SHA-256 fingerprints. Fingerprints
use the Phase 11 canonical input encoding, excluding derived rule flags. Both
complete per-field evidence snapshots are retained for reading historical reports;
this is private tenant-scoped lease data, not public analytics or operational logs.

## API, storage and access controls

All routes require the existing `ai:manage` administrator permission:

- `GET /api/v1/conflicts/config`: enabled flag, revision and explicit no-authority /
  no-external-call indicators.
- `POST /api/v1/conflict-comparisons`, strict body only:

  ```json
  {
    "request_key": "client-generated UUID",
    "left_run_id": "first workflow UUID",
    "right_run_id": "second workflow UUID"
  }
  ```

- `GET /api/v1/conflict-comparisons/{id}`: one stored comparison.
- `GET /api/v1/workflow-runs/{id}/conflict-comparisons?limit=20&offset=0`:
  records involving either side; maximum limit 50. Both source parents must still
  be visible. Hidden/archived counterpart records are omitted from history; offset
  applies to stored rows before visibility filtering, so a short page is not a total.

There is no caller-supplied tenant, text, field value, relationship, authority choice,
policy or model configuration. Both runs must be REVIEW_REQUIRED LEASE_FIELDS
results with extraction data, distinct source versions, nonarchived sources, PARSED
versions, matching checksums and fresh SQL citations equal to the recorded citations.
Changed source/unsupported relationship returns 409; hidden/foreign sources return
404. Fresh scalar user/org/session authorization is checked under row locks before
publication. Run/document locks are taken in stable ID order; composite tenant/source
FKs protect both sides and tenant-bound actor/session references. Stored hash columns
mirror the report hashes. SQL check constraints forbid same-run/same-version pairs.

New comparisons append separate SQL records. The API has no update/delete routes;
this is **not** a privileged-SQL-proof immutable audit ledger. Source visibility gates
old reports even when comparison execution is disabled. The same org/request UUID
replays the original oriented pair and report; reuse with different/reversed inputs
returns 409. New UUIDs append evaluations under the current engine. Old replay never
silently adopts changed engine code or incurs model spend.

Insert conflicts use a nested transaction/savepoint: a concurrent request-key winner
can be read without rolling back the surrounding request transaction. This same
transaction-safety fix applies to Phase 11 local rule evaluation. Collision tests
force a pre-insert miss and exercise the real unique constraint/savepoint/replay path;
that is **not** proof of real PostgreSQL concurrent scheduling.

## UI and workflow preservation

Documents → workflow history → a completed lease extraction now includes an
Amendment / version comparison panel. Paste the other run UUID from workflow
history and explicitly select “Compare stored extractions”. It does not automatically
start comparisons, query providers, change the extraction graph, or rewrite old
workflow request/revision semantics. Current run is right; pasted run is left.

The UI displays both source versions/upload times, source linkage/type snapshots,
latest-upload hint where applicable, paired values, exact quotes/page/offset context,
literal forms, reasons and fingerprints. Text is escaped React content. Duplicate or
unobserved proposals are shown rather than hidden. The UI retains the latest ten
visible comparisons; API pagination exposes older snapshots. A same-key retry pins
the original pair even after editing the other-run input. Errors remain visible and
never become an invented conflict-free result. History is readable with execution off.
This is a two-source comparison timeline, not a full effective-date/legal timeline.

## Configuration, migration and validation

Default remains off in application settings, `.env.example` and CI:

```sh
LEASEGUARD_CONFLICTS_ENABLED=true
```

Restart the backend after changing settings. Existing eligible results can be compared
even when LLM, workflow, rules and retrieval execution are off. Creating new source
extractions still requires every Phase 10/11 paid-workflow gate and explicit consent.
Do not enable paid extraction merely to inspect comparison history.

Run with the development virtualenv activated:

```sh
(cd backend && python -m alembic upgrade head)
./scripts/check.sh
(cd frontend && npm audit --audit-level=high)
```

`0011_conflicts` creates the ledger, dual source FKs/checks and indexes, preserves
existing tables/results, and advances readiness to phase12. Downgrade refuses when
any comparison exists. Preserve/export and explicitly reconcile records through a
reviewed maintenance plan before rollback; do not delete history to bypass the guard.
On an empty ledger it removes only its own schema and restores phase11. Prior
rule/lease migration rollback guards remain intact. Alembic now imports a complete,
router-independent ORM registry before schema comparison, avoiding incomplete CLI
metadata. PostgreSQL remains the supported configured runtime database; unit
migration tests execute SQLite operations directly, not by relaxing that runtime gate.

Tests cover repeatability/no mutation, 3-vs-5-year renewal proposals, unit/currency/
period ambiguity, numeric precision equivalence, missing/duplicate/conditional inputs,
source/normalization integrity, real parser/vector/mock-model preparation of both
version and amendment sources, no-network local comparison, immutable original JSON,
replay/disabled behavior, strict body, tenant/RBAC, source changes, counterpart archive
visibility, fresh role revocation, dual database scope constraints, unsafe rollback,
unique-collision replay and complete ORM registration. Frontend tests cover side-by-
side escaped evidence, chronology/non-authority labels, disabled readable history,
valid UUIDs, explicit no-consent/no-model comparison requests, pinned-pair retry,
uncertain errors and unobserved fields. Opt-in real-infrastructure workflow tests now
also compare two versions with provider HTTP explicitly mocked; those require real
PostgreSQL, Redis, MinIO and Qdrant and are not a claimed local deployment success.

Production legal accuracy, semantic clause alignment, whole-document absence,
authority decisions, real PostgreSQL concurrency, and paid-provider behavior remain
unvalidated. Use synthetic documents only. See the phase completion report for exact
check counts and dependency readiness.

**Stopped after Phase 12. Next only on authorization: Phase 13 — human review.**

### Local verification — 2026-10-16

- **247 backend tests passed; 11 skipped** (unavailable infrastructure variants and
  the opt-in paid-provider test). Ruff lint/format and mypy **78 source files** pass.
- **40 frontend tests passed**; Prettier, TypeScript and production build pass;
  npm audit reports **0 vulnerabilities**. The final fixture-only adjustment was
  additionally rechecked with all five comparison UI tests and TypeScript.
- SQLite migration/schema roundtrip and retained-data rollback guard pass; complete
  router-independent ORM registration is checked in a fresh subprocess. PostgreSQL
  forward/reverse SQL renders, including executable rollback refusal. This does not
  establish real PostgreSQL migration or concurrency execution.
- The combined check command exceeded the tool's 120-second wait near the end of
  backend tests; after confirming that process ended, the entire backend suite was
  run separately to completion (247/11). No success was inferred from partial output.
- Refreshed preview OpenAPI exposes all comparison routes and no-authority/no-overwrite
  constants. Four anonymous routes return 401. Frontend Documents shell and same-origin
  health proxy return 200; proxied comparison config returns 401.
- API liveness is 200; dependency readiness remains correctly **503/not_ready**.
  PostgreSQL and the other deployment dependencies remain unavailable, so migrations
  have **not** been applied to a live PostgreSQL deployment. Provision dependencies
  and run `python -m alembic upgrade head` before authenticated data workflows. The
  preview refresh only verifies routes/authentication, not a data-ready deployment.
- No paid model calls, real lease uploads or production legal-validation claims.
