# Phase 4 — originals, metadata and document versioning

Historical phase report. [Phase 5](phase-5.md) adds opt-in real parsing, source chunks
and durable jobs. New uploads still start unprocessed and NOT_SCANNED.

## Inspection and plan

Phase 3 already had an async PostgreSQL foundation, authenticated tenant context,
RBAC and a working identity UI. Documents were only a planned screen; no file
storage, document schema or upload route existed. The implementation plan was:
models/migration → storage adapters → bounded file validation → scoped service/API
→ document UI → failure/security/migration tests → operational documentation.
No authentication or foundation functionality was removed. Phase 5 processing is
not included.

## Implemented

- `documents`, `document_versions` and `document_metadata` tables, introduced by
  reversible `0003_documents`. All have organization ownership, with composite
  foreign keys for parent documents, uploaders, metadata and original versions.
- Original filename, canonical MIME, SHA-256, size, uploader, timestamp and version
  number retained per original. A version's object key is generated on the server
  from organization/document/version UUIDs, never from a client filename. Keys
  and storage credentials are not returned in document JSON.
- Explicit user-supplied labels: document type, property name, external reference,
  notes and logical title. These are **not** AI-extracted/classified facts. Metadata
  edits are currently document-level; immutable metadata/audit history is planned.
- A private S3-compatible adapter using boto3, plus an explicit local development/test
  filesystem adapter. Compose configures MinIO, persistent object volume and
  one-shot bucket initialization. API requests never auto-create buckets.
  S3 puts use conditional create (`If-None-Match: *`); previous original bytes have
  no mutation API. This is application-level immutability, not WORM/object locking.
- Paginated/filterable tenant-scoped listing, detail, metadata patch, original
  downloads, version listing/upload, parent links, archive and restore. Listing
  joins current versions/metadata in one data query plus a count query.
- Version creation locks the document row on PostgreSQL and checks the client's
  `expected_version`. Stale attempts return 409 rather than replacing a newer
  original. Identical content in one document's history returns 409; unrelated
  documents/organizations may independently upload identical bytes.
- Files begin at **UPLOADED**, **UNCLASSIFIED**, **NOT_SCANNED**, with unknown page
  count. No parsing, chunks, jobs, embeddings, extraction or AI approval is run.
  Status cannot be set by clients. Archive retains every byte; restore returns
  these unprocessed documents to UPLOADED. Archived files are still readable by
  authorized members but cannot be edited or versioned.
- Authorized downloads verify size and SHA-256 before returning content. Files are
  always attachment downloads with octet-stream, nosniff, sandbox and no-store;
  they are never rendered inline as same-origin HTML/PDF. UI warns before download.
- Actual document library UI: upload, title search, status filter, pagination,
  metadata editing, version history, downloads, new versions and archive/restore.
  It reads advertised limits/permissions from the API and clears per-user component
  state on identity changes. Documents are lazy-loaded to keep the main bundle small.

## File validation and limits

Default file limit: **20 MiB**, configurable from 1 KiB to 100 MiB via
`LEASEGUARD_MAX_UPLOAD_BYTES`. A raw request cap adds 64 KiB for multipart overhead;
it checks declared size and actual received bytes (including chunked requests).
The file reader separately enforces the file cap. Nginx's 101 MiB request ceiling
accommodates the largest allowed backend configuration; the backend's smaller
configured cap remains authoritative.

Accepted inputs:

- PDF: `.pdf`, compatible declared MIME (or octet-stream), `%PDF-` prefix and
  trailing EOF marker. **Signature check only**: full PDF parsing/validity,
  encryption/scans/page quality are Phase 5 concerns.
- DOCX: `.docx`, compatible MIME, ZIP entry/count/expanded-size/ratio checks,
  safe paths, no encrypted entries or obvious macro/ActiveX/embedded-object files,
  expected Word content-type and document XML root. Defused XML rejects unsafe
  entities. No archive contents are extracted to disk or executed.
- TXT: `.txt`, compatible MIME, UTF-8, nonempty text, no binary/control content.

Filenames are normalized and checked for path separators, hidden names, controls
and excessive length. File signatures/container checks **are not a virus scanner**.
Every accepted file remains NOT_SCANNED. Use synthetic data only; do not execute
or open originals from untrusted parties. Dedicated malware scanning/quarantine
release, sandboxed parsers, OCR and production upload abuse controls are not yet
implemented. The API never follows links or document instructions.

## Authorization and API

All document endpoints require an active Bearer-authenticated tenant principal.
Every role can read its organization's documents. Only `ORG_ADMIN`, `SUPER_ADMIN`
and `ANALYST` can upload/edit/archive/restore. SUPER_ADMIN has no tenant bypass.
There are no per-document ACLs beyond this explicit organization/role policy yet.
Supplied organization headers do not select a tenant; an extra multipart tenant ID
is ignored, while unknown JSON patch fields are rejected. Cross-tenant IDs, parents
and versions return 404 and never expose storage bytes.

Base: `/api/v1/documents`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/policy` | Configured cap, file extensions, caller upload capability, disabled parser/scanner flags |
| POST | `` | Multipart `file`; optional title, document_type, property_name, external_reference, notes, parent_document_id |
| GET | `` | Paginated `items/total/limit/offset`; optional `q` title substring and `status` |
| GET | `/{id}` | Latest version and metadata |
| PATCH | `/{id}` | Typed metadata/title update; null optional labels clear them |
| GET | `/{id}/versions` | Descending versions; `limit`/`offset` |
| POST | `/{id}/versions` | Multipart `file` plus required integer `expected_version` |
| GET | `/{id}/versions/{number}/download` | Authorized, integrity-checked original |
| POST | `/{id}/archive` | Retain originals; block edits |
| POST | `/{id}/restore` | Restore an archived, unprocessed document |

404 = not found in caller's tenant; 403 = role denied; 409 = stale/duplicate/version
or status conflict; 413 = cap exceeded; 415 = unsupported/invalid content;
422 = bad fields; 503 = unavailable/corrupt storage or database. OpenAPI is at
backend `/docs`. Storage failures never return synthetic success.

## Consistency and recovery boundaries

S3 and PostgreSQL are not a distributed transaction. Publication stores a unique
object, flushes document/version/metadata records, and explicitly commits before
returning 201. Before a commit attempt, known failed publication rolls the database
back and compensates a confirmed object write by deleting only that attempt's key.
Existing versions are never overwritten or deleted as compensation.

If a commit acknowledgement is uncertain, **retain the bytes**: deleting could
break a record that actually committed. Likewise a process crash/cancellation,
ambiguous storage timeout or failed compensation can leave orphan objects.
Operational events identify uncertain document/version UUIDs; inspect PostgreSQL
and the matching private object prefix together before manual reconciliation.
There is no automatic orphan deletion, retention job, outbox or exactly-once claim.
Do not delete objects merely because a stale listing does not show a DB record.
Backups must cover both database and object volumes consistently; no backup tooling
is implemented yet. Changing storage backend does not migrate existing objects.

## Setup

From a fresh clone, Docker Compose 2.24+:

```sh
python scripts/init_dev_env.py
docker compose up --build
```

The helper generates a JWT key and aligned random local MinIO credentials without
printing them. It never overwrites an existing `.env`. If upgrading from Phase 3,
add storage variables from `.env.example` to existing configuration; align
`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` with local Python's S3 access/secret settings.
Compose injects its container hostnames and storage settings explicitly. Existing
MinIO/PostgreSQL volumes retain their data; environment changes are not a data
migration or a guarantee that credentials have rotated.

Open `http://localhost:8080`, sign in/register, then Documents. Upload
`evaluation/fixtures/synthetic-commercial-lease.txt`. Add a changed file as a new
version, download both versions, edit labels, archive and restore. There are no
extracted lease fields to review yet. Development MinIO console is loopback-bound
at `http://localhost:9001`; do not make it or the bucket anonymously public.
MinIO images currently use the demo `latest` tag; pin/verify supported images and
security policies before any production deployment. Images/Compose are not tested
in this sandbox.

For local Python, with root `.env` created and copied to `backend/.env`:

```sh
. .venv/bin/activate
pip install -e './backend[dev]'
docker compose up -d postgres redis minio
cd backend
python -m app.documents.init_storage
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
# Another terminal: cd frontend && npm ci && npm run dev
```

Default local endpoint is `http://localhost:9000`; Compose uses `http://minio:9000`.
Bucket: `leaseguard-documents`. API credentials need only the relevant private
bucket/object operations. Provisioning requires additional bucket creation/list
permissions and is a separate explicit development command; demo Compose uses root
MinIO credentials, not a recommended production IAM policy.

An optional **development-only** alternative is
`LEASEGUARD_STORAGE_BACKEND=local` with
`LEASEGUARD_LOCAL_STORAGE_PATH=/a/private/writable/directory`. This is real local
file persistence, not an S3 emulator. It is excluded from Git. Production settings
reject local storage and require an explicit JWT key, S3 credentials and HTTPS.
At-rest encryption, bucket policy verification, dedicated IAM identities, object
lock, high-load streaming, resource quotas and backup/retention remain deployment
or later-phase work. Downloads are buffered within the bounded document size;
large-file multipart/resumable transfer is not implemented.

`/ready` now requires current migration baseline, Redis PING and object-store
connectivity/bucket existence. It does not prove every future operation/IAM grant
will succeed. `/health`/`/live` remain independent. Future Qdrant/Neo4j dependencies
are not falsely reported healthy.

## Tests and exact verification status

```sh
. .venv/bin/activate
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0003_documents:0002_identity --sql
# With migrated PostgreSQL, Redis and initialized private MinIO:
alembic upgrade head
alembic check
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

Locally verified: **63 backend tests passed, 5 live integration tests skipped; 8
frontend tests passed**. Ruff, mypy, TypeScript, formatting and production builds
passed. All migrations execute, match registered SQLAlchemy models and reverse on
SQLite. PostgreSQL upgrade/downgrade SQL renders offline. SQLite is test-only, not
accepted by runtime configuration, and cannot validate PostgreSQL concurrency.

HTTP tests use actual SQLite rows and local original files: tenant/role boundaries,
versioning, archives, pagination/search, metadata, MIME/signature errors, DOCX XML
entities/unsafe paths, size/chunked limits, SHA corruption, storage/DB publication
failures, compensation and preservation under uncertain commit. SDK stub tests
validate S3 put/get/delete/head contracts, not a live storage server. Frontend tests
use HTTP hooks only inside tests, verifying guest isolation, read-only controls,
file limits and real File/FormData submission with visible failure (no fake success).
jsdom does not exercise native browser file validation; a real-browser end-to-end
run is not claimed.

CI config provisions PostgreSQL/Redis/MinIO and enables real original round trips,
tenant-isolated downloads and simultaneous version attempts (one 201, one stale
409), in addition to prior auth concurrency/rate and rollback tests. These live
checks, images and Compose startup **have not run locally**: Docker/PostgreSQL/Redis/
MinIO binaries are absent. Remote CI success is not claimed. Sandbox Python is
3.11; packaging/CI/Docker target required Python 3.12. Existing Starlette/httpx
TestClient deprecation warning remains visible; it is not a test failure.

## Files/areas changed

- `backend/app/documents/`: models, schemas, storage, file validation, bounded body
  middleware, service, API and explicit bucket provisioning
- `backend/alembic/versions/0003_documents.py`, metadata registration
- Shared settings, RBAC capability map, API factory/errors/logging and readiness
- Shared test fixture, document/storage/migration and opt-in live integration tests
- `frontend/src/documents.tsx` and tests; workspace/status/styles/error messages;
  Nginx upload cap
- Compose/CI MinIO setup, dependencies, env example/helper and synthetic TXT fixture
- README, architecture, infrastructure notes and this runbook

Phase 4 ends here. Phase 5 adds real parsers, chunks, processing metadata and jobs.

Preview smoke checks through Vite passed: `/documents` and `/api/health` returned
200; unauthenticated document list/policy returned 401; readiness returned 503 and
explicitly marked absent object storage unavailable. OpenAPI includes multipart
version/upload/download routes with Bearer security. Compose/CI YAML and dependency
wiring parsed successfully. The environment helper was verified in a temporary
directory for aligned random S3/MinIO keys, private permissions and overwrite refusal.
