# Architecture and phased delivery

The repository was inspected before implementation: it contained only `README.md`.
Phase 1 establishes one frontend and one modular-monolith backend. No pre-existing
application functionality was replaced. The supplied specification requires stopping
at the end of each phase.

## Present architecture

Browser → React / React Router / TanStack Query → relative `/api` HTTP calls
→ Vite development proxy (or Nginx in Docker) → FastAPI → PostgreSQL / Redis.

Phase 2 adds configured async persistence, Alembic, logging/errors and readiness.
See [Phase 2 implementation and verification](phase-2.md).

`GET /api/health` reports process health only. API docs are at backend `/docs`.
The overview queries the actual backend. There are no fabricated lease metrics,
extractions, logged-in users, or completed AI workflows.

Phase 3 adds versioned `/api/v1` identity APIs, organization-scoped RBAC and
revocable token families. Signed identity resolves a trusted `CurrentPrincipal`;
all member/object queries constrain organization ownership. Database composite
foreign keys prevent cross-tenant session/token references. UI identity data is
real, and browser tokens are memory-only. See [Phase 3](phase-3.md).

Phase 4 adds authenticated document APIs and original-file storage through an
ObjectStore interface (S3/MinIO; explicit development filesystem adapter). Version
publication commits before a success response, compensates known pre-commit
failures and retains uncertain outcomes for manual reconciliation. Scoped SQL
predicates/composite FKs protect documents, versions, metadata and parents. No
AI results or malware-clearance claims are made. Phase 4 itself did not parse files.
See [Phase 4](phase-4.md).

Phase 5 adds a durable PostgreSQL processing queue and separate worker. Leased,
fenced claims publish normalized source/chunks atomically. Resource-bounded child
processes receive no service credentials. OCR remains an interface with explicit
OCR_REQUIRED failure, not fake extracted text. Queueing is opt-in for synthetic
unscanned data; production refuses that bypass. See [Phase 5](phase-5.md).

Phase 6 adds Qdrant and a separate durable index worker. SQL publishes a fenced
vector generation only after complete upsert; search filters tenant/generation and
revalidates source ownership/offsets after vector I/O. Default embeddings are an
honestly labeled offline lexical baseline; an explicit remote adapter supports
semantic vectors. No LLM answer is fabricated. See [Phase 6](phase-6.md).

Phase 7 adds source-backed manual graph assertions and a separate durable Neo4j
projection worker. Immutable SQL revisions, source hashes and generation fences
prevent stale/partial projections from appearing current. Typed relations and
bounded neighborhoods are tenant/version-scoped. No names are globally merged,
no relationships are automatically inferred, and graph assertions are explicitly
UNVERIFIED. See [Phase 7](phase-7.md).

Phase 8 introduces `app.ai` provider protocols, bounded no-retry HTTP transport,
strict text/structured OpenAI-compatible generation and profile-compatible embeddings.
An admin-only fixed synthetic probe uses explicit paid-egress consent, fail-closed
Redis admission and separately committed tenant-scoped SQL run/idempotency metadata.
No documents are sent by probes and no chat/extraction/workflow executes. LLM
generation defaults off; readiness never bills. Live provider calls remain
unverified. See [Phase 8](phase-8.md) for failure and uncertain-outcome semantics.

Phase 9 executes a real typed LangGraph with per-run service ports and a fixed,
bounded path: load/classify/retrieve/extract/schema/evidence/confidence/decision.
Extraction is limited to unverified title/heading candidates; legal schemas/rules
remain future work. Every completed run is REVIEW_REQUIRED, not approval or an
actual review task. Document egress is separately enabled and explicitly consented;
shared Redis admission reserves two generation units. SQL records durable run,
stage and call metadata; no hosted checkpoints/tracing or automatic paid resume.
See [Phase 9](phase-9.md) for privacy and unknown-outcome semantics.

Phase 10 extends that graph via explicit LEASE_FIELDS mode and a new schema/revision,
not an automatic upgrade of old candidate runs. Twelve domain-specific proposal
groups require exact values/quotes from scoped context; server-only literal parsers
normalize recognized formats after evidence checks. Uncertainty/conditionality is
preserved, empty groups mean only not observed in context, and semantic confidence
stays unknown. The shared SQL ledger stores mode and source-backed proposal results;
keys cannot be rebound to another mode. A grouped UI/schema catalog exposes actual
results without rules, canonical lease updates or approval. See [Phase 10](phase-10.md).

## Target domain architecture (mostly planned)

FastAPI modular monolith: Auth, Documents, Leases, AI, RAG, Rules, Reviews,
Audit, Analytics, Integrations. PostgreSQL for transactions and durable processing
jobs; Redis for auth throttling and future cache/event transport;
Qdrant for tenant-filtered retrieval; Neo4j for relationships; S3/MinIO for originals.
LangGraph orchestrates typed state with provider-independent LLM interfaces.
Documents are untrusted data. AI proposes extractions; deterministic validation,
evidence checks, confidence policies, conflict detection, and human approval
control subsequent decisions. Every persisted field must retain citations and history.

## Roadmap

1. Repository setup — implemented.
2. Backend foundation — implemented: configuration, database, migrations, logging, errors, readiness.
3. Authentication — implemented: organizations/users, JWT, rotating refresh sessions, RBAC, tenant isolation.
4. Document management — implemented: scoped original uploads, private S3/MinIO storage, metadata, versions, download integrity, archive/restore.
5. Parsing — implemented: PDF/DOCX/TXT interfaces, bounded parser child, source segments/chunks and durable PostgreSQL jobs.
6. Retrieval — implemented: Qdrant, explicit durable index generations, lexical/optional remote embeddings, mandatory tenant filters and SQL-backed citations.
7. Knowledge graph — implemented: source-backed manual UNVERIFIED typed entities/relations, immutable SQL revisions, leased Neo4j projection and scoped traversal.
8. Providers — implemented: typed LLM/embedding interfaces, real OpenAI-compatible HTTP adapter and strict structured output, opt-in admin-only synthetic diagnostic ledger/UI; live provider unverified.
9. LangGraph — implemented: typed bounded classification/retrieval/title-and-heading extraction, exact evidence checks, conservative confidence, durable stage/result ledger and admin UI; always REVIEW_REQUIRED, live-model path unverified.
10. Lease extraction — implemented: twelve typed proposal groups, exact field evidence and conservative literal normalization in independently enabled LEASE_FIELDS mode; model-proposed/unverified, completeness unknown.
11. Versioned deterministic rule engine.
12. Amendment and version conflicts.
13. Human review with reasons.
14. Complete audit trail.
15. Operational frontend workflows.
16. Real-time updates.
17. Notifications.
18. Analytics.
19. Security hardening and isolation tests.
20. AI evaluation against golden data.
21. Observability.
22. Explicitly labeled mock enterprise integrations.
23. Usage and billing architecture.
24. Production hardening and backups.

Each phase must be inspected, planned, implemented, checked, documented, and
reported before proceeding with user approval. Production deployment is blocked
until the remaining security, data lifecycle and production hardening checks are implemented.


## Phase 11 deterministic-rule boundary

Versioned local checks now annotate new explicitly rule-enabled lease workflows and
can separately re-evaluate stored eligible immutable extractions through the admin
API/UI. Policy/input hashes, complete rule versions and findings are retained; this
makes no legal-compliance, accuracy, completeness or approval claim. Unobserved
notice on partial input is indeterminate. Rules are off by default and add no model
calls; paid extraction still requires every existing gate. Migration 0010_rules
requires phase11 readiness and refuses destructive rollback when rule evaluations
or rules-enabled runs exist. SQL ledger append-only API is not a privileged-SQL-proof
audit trail. See [Phase 11](phase-11.md) for the catalog, configuration,
API contracts, verification and limitations. Human review/audit are now implemented; the Phase 12 section below is historical.


## Phase 12 explicit source comparison

The SQL-only, off-by-default conflict comparator now compares two distinct versions
or a directly linked user-labelled amendment using stored source-backed proposals.
Both values, evidence, source snapshots and fingerprints are retained without
rewriting historical extraction/rule data. Reports flag potential differences only:
partial context, model roles, semantic applicability, completeness and legal
authority remain unverified. Upload chronology is not supersession. No review task,
canonical update, approval or immutable audit service is created. Migration
0011_conflicts advances readiness to phase12 and refuses rollback with retained
comparisons; complete ORM metadata is registered for Alembic CLI checks. Real
infrastructure/paid-provider validation is still gated. See [Phase 12](phase-12.md).

## Stages 13–24 implementation update

Schema head is `0013_operations`; baseline phase24 denotes implementation only.
Human reviews, audit guards, connected workspace, opt-in live notifications, analytics,
explicit mock adapters, usage, evaluation, observability and backup tooling are now
present. Docker includes an operations-worker; runtime dependencies are hash-locked.
Deployment, live transports, actual PostgreSQL recovery/concurrency and legal-quality
validation remain unverified. See [status](completion-status.md), [operations](governed-operations.md)
and [production gates](production-readiness.md).
