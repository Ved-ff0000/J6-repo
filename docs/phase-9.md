# Phase 9 — typed, bounded LangGraph orchestration

## Scope and plan

Inspected the Phase 8 provider interfaces/diagnostic ledger, Phase 5 immutable parsed
source, Phase 6 retrieval/citations, authentication, migration and frontend boundaries
against the supplied Phase 9 requirement: **classification, retrieval, extraction,
validation and confidence** in LangGraph with strongly typed state.

Implemented: real per-run LangGraph → typed provider/schema boundaries → scoped
source/retrieval services → conservative evidence validation/confidence → durable
run/stage/call metadata → guarded APIs and parsed-document UI → tests and runbook.

**The extraction target in this phase is title and clause-heading candidates only.**
It is a real small extraction target, not a fake implementation of parties, dates,
rent or other Phase 10 schemas. A classification is a **model proposal from a partial
sample**, not a verified document type. Candidates remain MODEL_PROPOSED_UNVERIFIED.
Nothing updates a document's authoritative classification, graph, extracted lease
record, compliance finding, approval or review queue.

## Executing graph

`app.workflows.agent` compiles an actual LangGraph StateGraph with Pydantic state,
trusted organization/document/version scope, per-run injected service ports, no
uncontrolled global run state, fixed edges and a 16-step recursion limit:

```text
START → load_document → classify_document
                            ├─ LEASE / AMENDMENT
                            │    → retrieve_context → extract_candidates
                            │    → validate_schema → validate_evidence
                            └─ OTHER / UNKNOWN ──────────────────────────┐
                                                      calculate_confidence
                                                               ↓
                                                           decision → END
```

- **Load** reads up to the first three SQL chunks from the latest parsed, eligible,
  non-archived immutable version. Parsed job success and current-profile READY index
  are required. Chunk/page offsets are checked through the existing citation service.
  Context is capped to three chunks / 20,000 serialized source-JSON characters;
  an oversized first chunk fails rather than silently inventing context.
- **Classify** calls the real Phase 8 provider with a strict enum schema: LEASE,
  AMENDMENT, OTHER, UNKNOWN. Unsupported/uncertain kinds skip retrieval/extraction;
  the trace does not claim skipped stages completed.
- **Retrieve** calls the same reusable service as `/api/v1/search`, with mandatory
  authenticated tenant, exact document/version and fixed title/heading query.
  Qdrant generation/profile filters, SQL eligibility, source hashes, finite scores
  and citation checks are preserved. No SQL-only or fabricated retrieval fallback.
  No hits means FAILED, not made-up context. Default hashing is **lexical**, not
  neural semantic retrieval. Remote embeddings can incur their own charges.
- **Extract** makes one further structured provider call for at most three
  `document_title` / `clause_heading` candidates: exact value, quote and supplied
  chunk UUID. No other lease fields, tool requests, workflow decisions or caller
  prompts are accepted. System prompts treat source as untrusted data. This does
  **not** prove immunity to model manipulation; proposed labels still need review.
- **Validate schema** revalidates typed extraction; the provider adapter already
  rejects malformed JSON, unexpected fields, refusals, truncation and tools.
- **Validate evidence** requires a candidate's chunk to be in the retrieved context,
  its value to occur in its quote, and its quote to occur **exactly once** in that
  chunk. Duplicates, guessed/out-of-context chunk IDs, absent/ambiguous quotes and
  invented values fail the run. Output records exact page-relative quote offsets,
  source version and citation. Source/actor eligibility is rechecked between I/O
  boundaries and before final publication; revoked actors get 403, not results.
- **Confidence** computes exact evidence coverage over returned candidates. Empty
  extraction/unsupported classification gives null, not a fabricated percentage.
  `semantic_score` is explicitly **null**: no legal correctness, extraction
  completeness or calibrated semantic probability is available. A 100% quote-match
  ratio is not document coverage, accuracy, confidence or verified legal meaning.
- **Decision** always routes a completed run to **REVIEW_REQUIRED**. That label is
  not an approval, automatic validation or an actual human-review task. Review UI/
  reasons are Phase 13. No pretend rules/conflict/audit nodes mark future work done.

Nodes use injected service methods for persistence/access; they do not directly
modify critical document/lease state. The service owns final SQL publication.

Prompts `workflow_classification` and `workflow_extraction` are version 1.0.0,
authored 2026-10-16 with fixed descriptions/templates and a bounded source variable.
Run call records retain prompt identity, actual message hash, configured/reported
model, provider/profile, unknown separate model version, temperature, output cap,
reported usage, response ID, measured duration and recording timestamp. Raw prompts,
raw model output, secrets and upstream error bodies are not logged/persisted.

**Privacy change from Phase 8:** the workflow explicitly sends selected document
text to the configured provider. It is not the synthetic connectivity probe. Validated
candidate results also persist bounded source citations/text in SQL, tenant-scoped.
The full in-memory AgentState is not checkpointed or sent to hosted services.
Ambient LangSmith tracing is disabled in a run-local tracing context; no LangSmith
account/key is needed. Do not enable third-party HTTP/SDK verbose logs on private data.

## Admission, persistence and unknown outcomes

`LEASEGUARD_WORKFLOW_ENABLED=false` by default. Even if true, LLM and retrieval must
be enabled and the version must satisfy existing scan policy. The current scanner
is not implemented; unscanned development opt-in is **synthetic data only** and
production still refuses that bypass. No model is contacted at startup/readiness.

Only tenant-bound ORG_ADMIN/SUPER_ADMIN can run/read workflows. The body accepts
only UUID `request_key` and strict boolean `acknowledge_external_request:true`.
It accepts no tenant ID, source text, model/key/endpoint, tools or graph transitions.
The UI explains document egress and possible charges and requires confirmation.

Workflows and Phase 8 diagnostics share the same atomic Redis limiter and owned
inflight lease. A workflow conservatively reserves **two generation admission
units**, even if it stops after classification or fails before egress; a probe uses
one. Default shared limit is 3 units / 600 seconds per org. No refunds or automatic
model retries occur. Redis acquisition/ownership failures fail closed; a lost token
cannot start the next model call or unlock another owner. Busy → 409; limit → 429
with Retry-After. Embedding calls have their own existing bounds, not monetary
accounting in this generation-unit budget. This is **not an account-wide spend cap**:
new orgs, cache resets, key rotation, direct clients and other deployments can incur
more usage. Configure independent upstream budgets/alerts and restrict registration.

Migration `0008_workflows` adds the tenant-scoped SQL ledger with requester, session
and document/version composite tenant FKs, unique org/request key, source checksum,
revision, stage/trace, validated result, model-call metadata, safe errors and actual
timestamps. STARTED is committed before model egress. Stage STARTED/COMPLETED/FAILED
records and completed-call metadata commit separately; final status/result commits
before a successful response. These records are not tamper-proof/full Phase 14 audit.

This is a **bounded synchronous async-HTTP execution**, not a background durable
LangGraph task queue or checkpoint replay system. The graph deadline defaults to
150 seconds; each model call retains the provider timeout/output cap. Node retry
policies are explicitly empty. SDK tracing/remote checkpoints are not used.

A process crash, client timeout, interrupted checkpoint or final DB failure may
leave STARTED with a last-known stage. A recorded STARTED request returns 202 on
replay and is **pending or unknown**, never automatically resumed. A FAILED or
REVIEW_REQUIRED key replays its stored result without new egress. A key cannot be
rebound to a different version/document. The ledger must be retained/reconciled
before making a new paid key. Missing/unretained token usage is unknown, not zero;
failed, cancelled or malformed responses can still bill. Deleting/restoring a ledger
or cache is not a safe retry mechanism.

## API and UI

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/workflows/config` | Safe gate, revision, stage catalog and bounds; never calls a model |
| `POST /api/v1/documents/{id}/versions/{n}/workflow-runs` | Execute or safely replay an explicitly acknowledged request |
| `GET /api/v1/documents/{id}/versions/{n}/workflow-runs?limit=20&offset=0` | Scoped version history; limit 1–50 |
| `GET /api/v1/workflow-runs/{id}` | Scoped current run/stage/result, or opaque 404 |

A newly completed **FAILED** execution returns HTTP 200 with `status:FAILED` because
its ledger operation completed; this is not AI success. Inspect `status`/`error_code`.
Preflight/auth/admission errors use normal error envelopes/statuses. Revocation
before final publication is persisted as FAILED and returns 403 without result data.

In Documents → parsed version, admins see **Bounded evidence workflow** below the
retrieval/graph panels. Index the latest parsed version first. No request runs on
page load. History polls only while this UI's explicit request is pending; manual
refresh is read-only. It displays the actual trace, unknown outcomes, unverified
citations, partial context counts, coverage caveat and reported usage. The last key
is kept in component memory for safe explicit retry after an uncertain request. The
260-second client timeout exceeds the maximum configured graph deadline; DB/preflight
problems can still produce unknown outcomes. Archived sources hide result access
without erasing stored source/results. Explicit historical-version reads remain
historical; only new runs require the latest version.

## Upgrade and operate

With the supported Python 3.12 environment activated, from repo root:

```sh
pip install -e './backend[dev]'
# Configure existing DB/Redis/storage/retrieval settings as in earlier runbooks.
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Separate terminal from repo root:
cd frontend
npm ci
npm run dev -- --host 0.0.0.0
```

Or from root: `python scripts/init_dev_env.py && docker compose up --build` for the
existing local infrastructure stack. No new worker/provider container is required;
Compose already shares `.env`. Existing API installs must reinstall dependencies,
migrate and restart. Readiness now requires schema marker `phase9`. Rollback
removes the SQL run ledger, **not** external charges or retained vendor data.

Keep generation/workflows off until approved. For an explicit synthetic trial,
configure the server privately; never paste real keys into chat or commit them:

```dotenv
LEASEGUARD_RETRIEVAL_ENABLED=true
LEASEGUARD_LLM_ENABLED=true
LEASEGUARD_WORKFLOW_ENABLED=true
LEASEGUARD_WORKFLOW_TIMEOUT_SECONDS=150
# Set model, HTTPS endpoint and secret via the Phase 8 approved configuration path.
# LEASEGUARD_LLM_API_KEY must already be injected privately.
```

Workflow enablement requires at least two shared admission units and graph timeout
strictly above `2 * LLM timeout + 55` seconds (covers retrieval and margin).
The existing 30-second LLM timeout / 150-second graph defaults satisfy this. If
LLM timeout is 60, set graph timeout at least 180 (maximum 240). Review upstream
privacy/retention/region policy, budgets, allowlists and service credentials first.

## Checks and honest limits

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0008_workflows:base --sql
# Only with migrated disposable PG/Redis/MinIO/Qdrant/Neo4j infrastructure available:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

Sandbox: **159 backend tests passed, 10 opt-in tests skipped; 28 frontend passed**.
Ruff/formatting, mypy (61 modules), TypeScript, production build and npm audit pass.
Migration execution/model matching/reversal pass on SQLite; PostgreSQL forward/
reverse SQL renders offline. Tests execute **real LangGraph, real local parsers,
SQLite and Qdrant's local engine**, with explicitly mocked HTTP model responses.
They cover full/unsupported paths, exact evidence, schema/approval injection,
tenant IDs/FKs, actor/source changes, unknown-outcome replay, shared limits, Redis
outages, no-hit retrieval and suppression of ambient hosted tracing.

Nine infrastructure integrations (including the new real PG/Redis/MinIO/Qdrant
workflow test) and the explicit paid provider test are skipped locally because
servers/credentials are absent. CI is configured for infrastructure tests with
model HTTP mocked; no remote CI result is claimed. No actual LLM was called,
and provider hosts are excluded by sandbox egress policy. Live orchestration/model
accuracy, full browser end-to-end behavior and Docker builds remain unverified.
Mock contract responses do not establish real model quality, calibrated confidence,
production security, full lease extraction or production readiness.

Stopped after Phase 9. Next only on approval: Phase 10 evidence-backed lease schemas.
