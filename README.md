# LeaseGuard AI

**Automated Commercial Lease Intelligence & Compliance Platform**

An evidence-first, human-controlled vertical AI platform for commercial leases,
built incrementally as a modular monolith from the supplied specification.

## Delivery status

| Phase | Status | Implemented |
| --- | --- | --- |
| 1 · Setup | Implemented | Responsive React workspace, FastAPI bootstrap, Docker, CI |
| 2 · Backend foundation | Implemented | Settings, async SQLAlchemy, Alembic, PostgreSQL/Redis connectivity, JSON logs, errors, readiness |
| 3 · Identity | Implemented | Registration, organizations/users, Argon2id passwords, JWT, rotating/revocable refresh sessions, tenant-scoped RBAC, sign-in/member UI |
| 4 · Documents | Implemented | Tenant-scoped uploads, private object storage, metadata, immutable original versions, downloads, archive/restore and document UI |
| 5 · Processing | Implemented | Real PDF/DOCX/TXT parsers, normalized pages/tables, offset-backed chunks, durable DB jobs, bounded worker, processing UI |
| 6 · Retrieval | Implemented | Qdrant vectors, explicit durable indexing, mandatory tenant/generation filters, source-verified citations and retrieval UI |
| 7 · Graph | Implemented | Source-backed manual UNVERIFIED entities/relations, immutable graph revisions, durable Neo4j projection, scoped traversal and evidence UI |
| 8 · Providers | Implemented; live provider unverified | LLM/embedding contracts, real OpenAI-compatible adapter, strict structured output, bounded admin-only synthetic diagnostics and usage ledger/UI |
| 9 · LangGraph | Implemented; live-model path unverified | Typed bounded classification/retrieval/title-and-heading extraction, schema/evidence validation, conservative confidence, durable run history and UI; always review-required |
| 10 · Lease extraction | Implemented; live-model accuracy unverified | Twelve typed evidence-backed lease proposal groups, explicit mode, conservative literal normalization, uncertainty/coverage preservation, grouped source UI and schema catalog |
| 11–12 | Implemented | Deterministic evidence-backed rules and explicit version comparison |
| 13–18 | MVP implemented | Governed field review, guarded audit ledger, connected workspace, opt-in WebSockets, durable notifications and factual analytics |
| 19–24 | MVP tooling implemented; deployment unverified | Security controls, offline evaluation, observability templates, explicit mock integrations, usage meters, locked builds and backup tooling |

There are no fabricated lease metrics, successful AI outputs or signed-in users.
Enterprise adapters are explicitly **SIMULATED**, never claimed as external success.
**Not production-ready or deployed:** see [implementation status](docs/completion-status.md),
[governed operations](docs/governed-operations.md) and
[production launch gates](docs/production-readiness.md). Public registration must
be disabled in production; real files require additional identity/scanning controls.

## Architecture

```text
Browser → React / TypeScript / TanStack Query
                    ↓ relative /api requests
             Vite proxy / Nginx
                    ↓
          FastAPI modular monolith
          ├── Identity / tenant context / RBAC
          ├── Settings / logging / errors / health
          ├── Async SQLAlchemy → PostgreSQL
          ├── Auth throttling → Redis
          ├── Document originals → private S3 / MinIO
          ├── Durable parsing/index jobs → PostgreSQL queue → separate workers
          ├── Vector retrieval → private Qdrant + SQL source verification
          ├── Manual source-backed graph → private Neo4j + SQL provenance
          ├── Opt-in synthetic diagnostic → real LLM adapter + SQL/Redis guard
          ├── Opt-in typed LangGraph → bounded source/candidate workflow + SQL ledger
          └── Twelve-group lease proposals → typed schema + exact evidence + literal normalization
                    ↓
          Deterministic rules / comparisons / per-field human decisions
          Audit / notifications / analytics / explicit mock adapters / usage
```

See the [24-phase architecture plan](docs/architecture.md),
[Phase 2 foundation runbook](docs/phase-2.md),
[Phase 3 identity runbook](docs/phase-3.md),
[Phase 4 document runbook](docs/phase-4.md),
[Phase 5 processing runbook](docs/phase-5.md), and
[Phase 6 retrieval runbook](docs/phase-6.md), and
[Phase 7 graph runbook](docs/phase-7.md) and
[Phase 8 provider runbook](docs/phase-8.md).

## Technology stack

Frontend: React 19, TypeScript, Vite, Tailwind CSS, React Router, TanStack Query,
Axios, React Hook Form, Zod and Lucide. date-fns formats document timestamps; Recharts is reserved for future analytics.
Vitest, Testing Library and jsdom test identity and document UI state.
Backend: Python **3.12+**, FastAPI, Pydantic v2 / Settings, SQLAlchemy 2, asyncpg,
Alembic, Redis, PyJWT and pwdlib/Argon2id. Pytest, Ruff and mypy provide checks.
Runtime persistence is PostgreSQL; private originals use boto3/S3-compatible storage.
pypdf performs PDF parsing; safe Word XML parsing preserves DOCX text/tables.
SQLite/local files are used for isolated tests; an explicit development filesystem
adapter is also available. Upload validation uses python-multipart and defusedxml.

## Quick start — Docker

Prerequisites: Docker with Compose **2.24+** and Python for the local key helper.

```sh
python scripts/init_dev_env.py  # creates .env once with a random signing key
# If .env exists, leave it intact and configure its key as described in the runbook.
docker compose up --build
```

Open `http://localhost:8080/register` to create a synthetic organization. Open
Settings to create/manage test users, then Documents to upload
`evaluation/fixtures/synthetic-commercial-lease.txt`. Versions retain original bytes;
parsing is an explicit separate step, with no AI extraction or malware clearance.
Backend OpenAPI is at `http://localhost:8000/docs`. The frontend proxies `/api/*`.
Compose starts PostgreSQL, Redis and MinIO, initializes the private document bucket,
runs migrations, then starts the readiness-gated backend/frontend. PostgreSQL and
MinIO use persistent volumes. `docker compose down` stops
services without deleting those volumes. There is no backup system yet.

## Local development

Prerequisites: Node **22.12+** (or supported newer LTS), npm, Python **3.12+** and
PostgreSQL/Redis/MinIO (or Docker Compose for those dependencies).

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]'
python scripts/init_dev_env.py  # skip if already created
cp .env backend/.env           # don't overwrite custom settings
docker compose up -d postgres redis minio
cd backend
python -m app.documents.init_storage
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
```

In a separate terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Run migrations after every schema change.

## Configuration

See `.env.example`. Backend variables use the `LEASEGUARD_` prefix. Settings load
from the process environment and `.env` relative to the backend working directory.
Compose optionally injects root `.env`; container DB/Redis addresses override local
URLs. Backend local runs from `backend/` use `backend/.env`.

Set `LEASEGUARD_JWT_SECRET` to at least 32 random bytes. The local helper generates
one and aligned random local MinIO credentials without printing them or overwriting
configuration. Upgrading users must add the Phase 4 storage settings to existing
`.env` files; the helper never overwrites them. Development without an explicit
key uses a per-process ephemeral key; production requires an explicit key. Multiple
workers require a shared signing key. Development database passwords must not be
used in production. No GitHub token or LLM API key is required.

`API_PROXY_TARGET` optionally overrides the Vite proxy's backend URL (export it in
the shell before starting Vite). Browser code always calls relative API URLs.

## API and identity

Identity APIs are versioned under `/api/v1`: registration/login/refresh/logout/me,
current organization and scoped member administration. OpenAPI documents schemas,
Bearer security and common errors. See the Phase 3 runbook for the capability matrix.
Registration creates a new organization and its first admin; it never joins an
arbitrary existing organization. Member writes take the tenant from authenticated
context, not client-supplied IDs. SUPER_ADMIN is not a cross-tenant bypass.

JWT access tokens are short-lived; opaque refresh tokens rotate once and are stored
only as hashes. Replay/logout/access changes revoke server-side sessions. The UI
keeps tokens **in memory only**, so a page reload requires signing in again. It
single-flights refresh requests and clears cached member data on identity changes.

`/health` and `/live` (also `/api/health`, `/api/live`) report process liveness.
`/ready` and `/api/ready` check PostgreSQL's current schema baseline, Redis and private object-store connectivity:
HTTP 200 only when all required checks pass (including Qdrant/Neo4j when enabled),
otherwise sanitized HTTP 503. An alive API is not
necessarily ready. Qdrant connectivity is checked only when retrieval is enabled;
Neo4j connectivity is likewise checked only when graph projection is enabled.

## Document management

`/api/v1/documents` provides multipart upload, paginated title/status search, detail,
typed metadata edits, parent-document links, original downloads, version history,
new versions with optimistic version checks, and archive/restore. All reads/writes
are tenant-scoped; all roles read their own org, while admins/analysts write.
`/api/v1/documents/policy` advertises current limits and caller capabilities.

Supported formats are PDF (signature validation), DOCX (safe container/XML checks),
and nonempty UTF-8 TXT. Default file cap is 20 MiB, with independent multipart-body
limits. Original downloads verify checksums and always use attachment/no-store/
nosniff headers. Every new upload starts UPLOADED / UNCLASSIFIED / NOT_SCANNED.
Metadata labels are user-supplied, not evidence-backed extraction. Real parsing is opt-in via the durable worker; no antivirus, OCR or inline original
preview is implemented. Only use synthetic content.

Object keys/credentials are never returned in document JSON. Storage and DB are
not a distributed transaction: known pre-commit failures compensate unique objects;
uncertain commits/crashes retain bytes for manual reconciliation. No automated
orphan deletion or exactly-once guarantee exists. See the Phase 4 runbook.

## Document processing (Phase 5)

Explicit processing jobs use a durable PostgreSQL queue and a separate worker,
not in-memory API background tasks. PDF parsing uses real physical page numbers;
DOCX/TXT have logical segments, with DOCX table rows preserved. Normalized source
and deterministic chunks retain exact source offsets and version ownership.

In Documents select **Parse this version**; active jobs are polled every two seconds.
PARSED is not AI-extracted, indexed, approved or malware-cleared. Scanned PDFs fail
with OCR_REQUIRED until an OCR provider exists. Failed jobs expose safe codes and
can be retried explicitly within limits; expired leases recover bounded attempts.
Results publish atomically and stale claim tokens cannot overwrite them.

Code defaults processing **off**. The development `.env.example` explicitly enables
`LEASEGUARD_PROCESSING_ENABLED` and `LEASEGUARD_ALLOW_UNSCANNED_PROCESSING` for
synthetic data only. Existing `.env` users must add both; nothing is overwritten.
Production rejects the unscanned bypass. The separate worker runs in Compose or
locally from `backend/` with `python -m app.processing.worker` using the same settings.
It needs POSIX/Linux resource-limit support. The limited parser child receives no
service secrets, but is **not a fully hardened sandbox**. See the Phase 5 runbook.

## Source retrieval (Phase 6)

After parsing, explicitly **Index parsed source**, wait for index READY, and search
that version's evidence. Qdrant returns real vector matches; PostgreSQL rechecks
ownership, current eligibility and exact source slices before any quote is returned.
Citations retain immutable version, page/segment, section, chunk and source offsets.
No generated answer or compliance confidence is invented.

The default is **real offline lexical hashing**, not a neural semantic model; UI
and API say so. An optional OpenAI-compatible embedding service is implemented for
semantic vectors, but requires explicit approved configuration/key and sends chunk
text/queries to that service. Its live service was not tested here. No model is
silently downloaded and no paid service is called by default. Generation stays disabled
unless explicitly configured; see Phase 8 below.

Code defaults retrieval off; `.env.example` enables it for synthetic development.
Existing `.env` users must explicitly add `LEASEGUARD_RETRIEVAL_ENABLED=true` and
`LEASEGUARD_QDRANT_URL=http://localhost:6333`. Compose adds private persistent
Qdrant and a separate index worker (`python -m app.search.worker` locally). Index
publication uses durable leased jobs and generation fencing; partial/stale writes
remain invisible. Rebuild is explicit; old vectors are retained, not erased by archive.
See the Phase 6 runbook for profile changes, filters, privacy and operational limits.

## Knowledge graph (Phase 7)

The parsed-source panel accepts explicit manual graph proposals for tenants,
landlords, leases, properties, clauses and related entity types. Every node and
relationship needs source chunk IDs from the same immutable version; node names
must occur in cited source. All assertions remain **HUMAN_ASSERTED_UNVERIFIED**.
Source linkage does not verify a party role, relationship meaning or legal fact.

PostgreSQL retains immutable revisions/provenance and durable projection jobs.
A separate graph worker writes real parameterized Neo4j transactions; only a
complete, fenced generation is published. Scoped reads check Neo4j against SQL
and source hashes, with bounded neighborhood traversal and citation inspection.
No arbitrary Cypher endpoint or automatic cross-document name merging exists.

Compose adds private persistent Neo4j and `graph-worker`. Code defaults graph off;
`.env.example` opts in for synthetic development. Existing `.env` users must add
explicit enable/connection/password settings; see `docs/phase-7.md`. The local
helper generates matching passwords for fresh setups without printing them.
No runtime mock graph, AI extraction, approval or inferred relationship is supplied.

## Tests and checks

With the environment activated and dependencies installed:

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql  # offline migration SQL generation
# With migrated PostgreSQL, Redis, initialized MinIO, Qdrant and Neo4j available:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

The script runs frontend format/type/tests/build and backend lint/format/mypy/pytest.
CI supplies PostgreSQL/Redis/MinIO/Qdrant/Neo4j, opts into live integration tests, checks schema drift,
and tests migration downgrade/upgrade. Use disposable data for destructive rollback.

Sandbox verification: **196 backend tests passed, 11 opt-in tests skipped; 31
frontend tests passed**. Formatting, typing, builds and offline migration rendering
passed. Migration execution/schema matching/reversal also passed on SQLite;
PostgreSQL concurrency is reserved for live tests. `npm audit` reports zero vulnerabilities. Python here is 3.11; packaging,
CI and Docker target 3.12. Docker/PostgreSQL/Redis/MinIO/Qdrant/Neo4j server binaries are absent, so live
service tests, migrations and container builds are **not locally verified**. Remote
CI and full browser end-to-end execution are not claimed.

## AI configuration and evaluation

Phase 8 adds real `LLMProvider`/`EmbeddingProvider` interfaces and an OpenAI-compatible
HTTP adapter for text and strict structured generation. Generation defaults **off**;
keys stay server-side. `/providers` lets organization admins explicitly acknowledge
a potentially paid **fixed synthetic** diagnostic, with per-org Redis admission,
durable request-key replay protection and scoped metadata/history. The Phase 8
probe sends no customer content. Existing embedding profile IDs are preserved.

No real LLM call was made here: credentials were not supplied and provider hosts
are outside sandbox egress policy. Contract tests do not establish live connectivity.
Readiness never probes or bills a model. See [Phase 8 runbook](docs/phase-8.md) for
private configuration, migration, strict limits, uncertain outcomes and opt-in tests.
Parsing/retrieval/manual unverified graph proposals still work without an LLM.
Phase 9 adds a real typed LangGraph execution path, off by default. Parsed-document
admins can explicitly consent to **document-text egress** for partial classification,
scoped real retrieval and at most three title/heading candidates. Schema/evidence
checks, call metadata and stage traces are persisted. The workflow shares provider
admission, never automatically retries/resumes paid calls, and always returns
REVIEW_REQUIRED rather than approval. Semantic confidence stays unknown; exact
evidence coverage is not legal accuracy or completeness. No real review task is
created. See [Phase 9 runbook](docs/phase-9.md).

Phase 10 adds an independently enabled `LEASE_FIELDS` workflow mode with all twelve
typed groups: parties, dates, rent, renewals, termination, notices, obligations,
insurance, maintenance, assignment, CAM and escalation. Every proposed value keeps
its exact source evidence and unverified status. Only unambiguous literal formats
normalize; no guessed currencies, locale dates or deadlines. Empty groups mean
not observed in the inspected context, not absent from the lease. Context/output
remain bounded; document completeness, semantic confidence and legal validity are
not established. Old candidate runs retain their mode/revision/results; retry keys
cannot switch mode. The grouped parsed-document UI exposes evidence and uncertainty
without approval or rule claims. See [Phase 10 runbook](docs/phase-10.md). The
[evaluation plan](evaluation/README.md) targets Phase 20. Original documents are
untrusted data, and future AI outputs must undergo deterministic checks and review.

## Versioned deterministic rules (Phase 11)

Eight immutable code-reviewed rules now check exact source linkage, explicit model
interpretation, literal normalization, positive durations, money precision, proposed
date order, termination-notice observation and renewal markers. Rules are off by
default (`LEASEGUARD_RULES_ENABLED=false`); `LEASEGUARD_RULES_DISABLED` accepts a JSON
array of known IDs. New rule-enabled lease runs pin the full policy, versions and
fingerprints; old workflow results remain unchanged. Admins can locally re-evaluate
stored eligible lease extractions without model calls and inspect separate history
in the Documents workflow panel. Unobserved/ambiguous partial-context inputs remain
indeterminate, not invented missing-clause failures. Even PASS is **not legal approval**;
all proposals remain unverified and REVIEW_REQUIRED. See [Phase 11 runbook](docs/phase-11.md)
for API, configuration, eight-rule catalog, migration/rollback guard and limits.

## Amendment / version comparison (Phase 12)

Administrators can explicitly compare two stored lease extractions from distinct
versions of one document or a directly linked, user-labelled amendment. The local
engine preserves both values/evidence and appends a comparison snapshot without
model calls or historical rewrites. Missing/ambiguous fields remain unresolved;
differences are **potential conflicts**, not established legal contradictions.
Version chronology is displayed separately from legal authority, which remains
NOT_ESTABLISHED/REVIEW_REQUIRED. Execution is off by default via
`LEASEGUARD_CONFLICTS_ENABLED=false`. Documents workflow history includes paired
values, a two-source timeline, findings and same-pair/key retry. See the
[Phase 12 runbook](docs/phase-12.md) for contracts, examples, limits and migration.

## Security and deployment limitations

This is **not production-ready**. Authenticated tenant-scoped identity is implemented,
but MFA, email verification, invitations, recovery/password reset/change, immutable
audit records, RLS policies, full abuse controls, TLS/CSP hardening, retention and
backups are not yet implemented. Local HTTP is for isolated development only.
The limiter fails closed if Redis is unavailable and uses socket peer addresses;
trusted-proxy and edge rate-limit configuration require deployment-specific review.
No cross-origin auth or auth cookies are enabled; Bearer credentials are sent
explicitly, not through ambient cookies. In-memory tokens do not eliminate XSS risk.

Do not upload real leases: original retention exists, but malware scanning and
production AI validation are not implemented. Parsing is only permitted explicitly for synthetic
development inputs pending a real malware-clearance workflow. Use synthetic organizations/users/documents only. Admin-created passwords require a secure handoff; no email
invitation is sent. All future data endpoints must use the trusted tenant context.
Application logs are operational logs, not the future immutable audit trail.
Backend/dependency ports are loopback-bound in Compose; frontend is exposed for
local/preview use. Never commit secrets or store them in frontend build variables.

## Screenshots

Run the workspace to view the overview, `/login`, `/register`, Settings and Documents. No
static screenshots or fabricated customer data are committed.

## Repository layout

- `frontend/` — typed React workspace, identity UI/tests, Vite/Nginx, Docker
- `backend/app/` — foundation, async DB infrastructure, identity/RBAC modules
- `backend/alembic/` — reversible database migrations
- `backend/tests/` — unit/HTTP tests and opt-in PostgreSQL integration tests
- `infrastructure/` — infrastructure notes
- `docs/` — architecture, phase runbooks and limitations
- `evaluation/` — planned AI evaluation scope and a clearly synthetic TXT upload fixture
- `scripts/` — validation and non-overwriting development environment helper
- `.github/workflows/` — CI

**Next: Phase 13 — human review.**
User authorized uninterrupted continuation through stages 13–24. These MVP modules
are now implemented; production validation remains pending. The earlier stage-specific
reports below are historical test snapshots, not current deployment evidence.

## Current validation — stages 13–24

Final sandbox regression: **278 backend tests passed / 11 infrastructure-dependent
skips; 46 frontend tests passed**. Formatting, Ruff, mypy (93 files), TypeScript and
production frontend build passed. npm audit reports zero vulnerabilities. Eight
synthetic golden labels validate; no model predictions/accuracy were invented.
PostgreSQL migration SQL rendered through 0013; live PostgreSQL was unavailable.
See [validation report](docs/validation-report.md) for exact commands and limitations.
The running API is live but **not ready (503)** until required services are provisioned.
