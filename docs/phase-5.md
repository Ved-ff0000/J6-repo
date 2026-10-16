# Phase 5 — deterministic parsing, chunks and durable jobs

## Inspection and scope

Phase 4 retained original files and versions in private storage with tenant-scoped
APIs. It had no parser, normalized source, chunks or processing worker. Plan:
parser interfaces → bounded subprocess execution → schema/migration → durable
queue and recovery → scoped status/source APIs → UI → tests and documentation.
Existing uploads/downloads/authentication remain available. No Qdrant, embeddings,
Neo4j, LLM extraction, business rules or approvals are introduced in this phase.

## Implemented

- `DocumentParser` interface with real `PDFParser` (pypdf), `DOCXParser` (safe Word
  XML/container handling) and `TextParser` (UTF-8). Input links and embedded text
  never become instructions, shell commands or network fetches.
- PDF text by **physical page**; encrypted PDFs fail with `ENCRYPTED_PDF`. Low-text
  image pages conservatively require OCR. `OCRProvider` is an interface only;
  `DisabledOCRProvider` returns `OCR_REQUIRED`. No fabricated OCR text exists.
- DOCX paragraph text and structured table rows/cells; explicit paragraph page
  breaks delimit **logical segments**, not reliable rendered/printed pages. TXT
  uses logical segments (normally one). No claim of perfect reading order, Word
  pagination, font/layout reconstruction or PDF table recognition is made.
- Normalized source text, heuristic headings and table metadata. Headings are
  simple numbered/uppercase/section heuristics, not legal clause interpretation.
- Deterministic overlapping chunks with version ID, tenant, source page/segment,
  section hint and exact Unicode code-point offsets into normalized page text.
  Chunks do not cross page/segment boundaries. Original bytes remain unchanged.
- `0004_processing` adds durable jobs, normalized pages, chunks and per-version
  processing fields. Composite foreign keys bind version/document/organization,
  requester and source page. One job per immutable version; old version text remains
  available after newer uploads. A future parser-revision reprocessing policy
  requires explicit implementation rather than silently replacing successful data.
- A separate CLI worker polls a **PostgreSQL-backed queue**; API requests never
  run processing in an in-memory background task. Queue insertion and document
  status are one committed DB transaction; there is no DB-to-Redis enqueue gap.
  Redis continues to support auth throttling, not processing durability.
- Claim order: document lock, then job lock (`SKIP LOCKED` on PostgreSQL). A claim
  has a random fencing token, attempt count and finite lease. Expired jobs can be
  reclaimed. Old tokens/expired ownership cannot publish. Max-attempt exhaustion
  records `WORKER_LOST`; no infinite automatic retries. Failed jobs need an explicit
  authorized retry, while stale-worker recovery is automatic within the attempt cap.
- Requester membership/role/org status is checked when claiming and before result
  publication. Workers consume the internal queue across tenants; every version/
  result write is bound to the claimed organization. There is no public global job API.
- All pages, chunks, counts and success status commit atomically. A partial insert
  failure rolls back all results. Successful results are not overwritten on repeat
  scheduling. Source SHA/size are verified before parsing. Failures store only safe
  codes, never parser exception text or document contents.
- UI schedules work explicitly, polls active jobs every two seconds, shows actual
  coarse stage/progress milestones, failures/retry, and source text inspection for
  current or historical versions. Text is rendered escaped, not as HTML. Queue
  acceptance is never described as completed analysis. No WebSocket is claimed
  (real-time transports are Phase 16).

## Processing policy and security boundary

Code defaults are **off**:

```text
LEASEGUARD_PROCESSING_ENABLED=false
LEASEGUARD_ALLOW_UNSCANNED_PROCESSING=false
```

The development `.env.example` explicitly enables both for **synthetic data only**.
Existing `.env` files are never overwritten: add these two settings yourself if
upgrading. A disabled parser returns 503 on schedule; unscanned processing without
explicit development permission returns 409. Uploads are still NOT_SCANNED after
parsing. Production settings reject the unscanned bypass, so current unscanned
originals cannot be processed there until a real malware-clearance workflow exists.

Parsing uses a separate POSIX child process with:

- Explicit environment containing no JWT, DB, Redis or object-storage credentials.
- Private temporary working directory; no shell, original filename arguments,
  inherited request contents on stdout, or document-triggered tool calls.
- 512 MiB virtual-address-space limit, 30 CPU seconds, 32 MiB output-file limit,
  wall timeout (default 45 seconds), bounded source/output sizes, pages, characters
  and chunks. Parent validates the result and re-derives chunks; source offsets
  are not trusted merely because a child produced them.
- Killed process group on a supervised timeout/cancellation; parser logs/stdout/
  stderr are discarded rather than becoming a content/secret logging channel.

**These are resource limits, not a hardened security sandbox.** There is no seccomp,
network namespace or complete filesystem isolation. Workers need Linux/POSIX
(`resource`/process-group support); native Windows worker execution is unsupported.
A hard worker crash can leave a child until its resource limits/host supervision
stop it; use external process/container supervision and isolation in production.
Only use synthetic development documents. OCR, malware scanning, parser vulnerability
review, input-specific parser tuning and container hardening remain future work.

## States and semantics

```text
UPLOADED → explicit schedule → PROCESSING (job QUEUED)
         → worker claim → PARSING (job RUNNING, milestone 10%)
         → parse + atomic persist → PARSED (job SUCCEEDED, 100%)
         └── failure → FAILED (safe code, no partial source result)
```

Percentages are persisted stage milestones, not invented estimates of parser CPU
completion. PARSED does **not** mean indexed, extracted, validated, approved or safe.
Classification remains UNCLASSIFIED. PDF `page_count` is physical; DOCX/TXT leave
it null and expose `parsed_segment_count` plus `page_number_kind=LOGICAL` instead.
OCR_REQUIRED blocks the whole job instead of publishing incomplete scanned content.
Very short image/logo pages can conservatively trigger this heuristic.

While queued/running, version/metadata/archive edits are blocked. Parsed/failed
originals can receive new versions (new latest starts UPLOADED). Archive retains
source results; restore recovers the latest version's processing status. Requesting
an old version's new processing returns 409; reading old source remains allowed.
Failed retry uses the same job, bounded attempts and current requesting member.
Policy failures may occur before an attempt is consumed. No successful job is
silently rerun, and no automatic content or source-result deletion is introduced.

## API

Base `/api/v1/documents/{document_id}/versions/{version_number}`:

| Method | Route | Meaning |
| --- | --- | --- |
| POST | `/process` | Explicit durable schedule; idempotent per version; returns existing job if already requested |
| GET | `/job` | Actual job or null if not requested |
| POST | `/retry` | Explicit retry of failed latest version, subject to policy/attempt cap |
| GET | `/pages?limit=5&offset=0` | Normalized source pages/segments, heuristic headings, DOCX tables |
| GET | `/chunks?limit=25&offset=0` | Offset-backed chunks and parser revision |

Readers require the same active tenant membership as original downloads. Writes
require ORG_ADMIN/SUPER_ADMIN/ANALYST in the caller's org. Unknown or other-tenant
IDs return 404. Schedule/retry return 202; callers must inspect returned **status**
(the same terminal job can be returned idempotently). No presigned public content
URL or organization selector is accepted. OpenAPI documents schemas/security.

## Run

Fresh development setup:

```sh
python scripts/init_dev_env.py
docker compose up --build
```

Compose adds a separate `worker` using the same DB/storage settings, after API
readiness; container init/restart and memory/process-count limits are configured.
All services are still a modular-monolith deployment, not business microservices.
The queue survives worker restarts in PostgreSQL. A ready API **does not prove a
worker is alive**; there is no worker heartbeat/health metric yet. If jobs remain
QUEUED, check flags and `docker compose logs worker` before claiming a result.

Upgrade existing environments:

```sh
# Preserve your .env and add these explicitly for synthetic development only:
# LEASEGUARD_PROCESSING_ENABLED=true
# LEASEGUARD_ALLOW_UNSCANNED_PROCESSING=true
docker compose up --build
```

Local app/worker, from `backend/`, after activating the Python 3.12+ environment,
installing updated `.[dev]`, starting services, and running `alembic upgrade head`:

```sh
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
# Another terminal with the same environment and backend working directory:
python -m app.processing.worker
# Optional single queue pass (useful for supervised jobs/tests):
python -m app.processing.worker --once
```

Open Documents, upload `evaluation/fixtures/synthetic-commercial-lease.txt`, select
it, and click **Parse this version**. Inspect PARSED source and offset-backed chunks
via the API. Upload a changed version to preserve the earlier original/source.
No AI provider key or GitHub token is needed.

Configuration also exposes page/character/chunk caps, chunk size/overlap, wall
seconds, lease seconds, max attempts and polling interval in `.env.example`. The
lease must exceed parser timeout plus 30 seconds for storage/persistence. Default:
500 pages, 2 million characters, 3,000 chunks; 2,000-character chunks with 200
characters overlap; 120-second lease; 3 attempts. Lease is not heartbeated during
this bounded operation; unusually slow I/O/publication can cause reclaim instead
of success. Use synchronized clocks and deployment supervision. No fair-share
scheduler, per-org queue quota or cancellation API is implemented yet.

## Tests and verification

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0004_processing:0003_documents --sql
# With real migrated PostgreSQL/Redis/MinIO:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

**75 backend tests passed, 5 live integration tests skipped; 12 frontend tests
passed** in this sandbox. Formatting, mypy, TypeScript and production builds pass.
All migrations execute, match registered models and reverse on SQLite, and render
PostgreSQL SQL offline. Python available here is 3.11; packaging/CI/Docker target
3.12. Docker/PostgreSQL/Redis/MinIO are absent, so live service/concurrency/container
execution is **not locally verified**; remote CI success is not claimed.

Local tests use actual synthetic PDF generation/pypdf text, DOCX XML/tables, TXT,
real bounded child processes and file-backed SQLite. They cover source offsets,
limits, encrypted/scanned PDFs, malformed originals, failed storage, retries,
partial-write rollback, requester revocation, stale claim fencing and exhausted
leases. UI tests cover escaped source/logical labels, disabled policy, OCR errors
and queue acceptance without fake completion. The prior Starlette/httpx TestClient
deprecation warning remains visible. No full browser end-to-end run is claimed.

CI's existing real storage/document integration now also parses real originals,
checks tenant-isolated chunks, races two PostgreSQL claimers, reclaims an expired
lease and verifies stale ownership cannot publish. These live checks are configured,
not claimed to have executed here. PostgreSQL-specific lock behavior cannot be
proven by SQLite tests alone.

**Destructive downgrade:** stop API writes/workers first. Downgrade drops jobs,
pages and chunks, resets processing document states to UPLOADED (retaining archive
state) and clears parser-derived page counts. Original objects/version bytes are
not deleted. Back up and review before any schema rollback.

## Changed areas and stopping point

`backend/app/processing/` (parsers, child runner, models, worker, API);
`0004_processing` and metadata registration; document version/state/policy logic;
settings/readiness/logging; parser/worker/migration/integration tests; frontend
processing panel/tests, document state controls and phase display; Compose worker,
Python dependencies, environment example; architecture/README/runbooks.

Stopped at Phase 5. Next: **Phase 6 — tenant-filtered Qdrant retrieval and citations**.
