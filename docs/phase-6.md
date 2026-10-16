# Phase 6 — Qdrant retrieval and source citations

## Scope and design

Inspection found immutable originals, normalized pages/chunks and durable parsing
jobs from Phase 5, but no embeddings, vector index, search or citation API. This
phase adds the **retrieval portion of RAG**, not an LLM answer generator. Plan:
embedding profiles → Qdrant adapter → durable publication manifests → authorized
index worker → filtered retrieval/source validation → UI → tests/runbook.

Implemented under `backend/app/search/`:

- Real embeddings, vector indexing and cosine retrieval through Qdrant.
- Durable `document_index_jobs` in reversible migration `0005_retrieval`.
- Explicit per-version indexing, status, retry and rebuild; separate worker.
- Mandatory tenant and published-generation filters in every Qdrant search.
- Current metadata filtering in PostgreSQL; page/section filtering in Qdrant,
  checked again against stored source before returning hits.
- Source-backed citations, standalone citation rechecks and document UI.
- Conditional Qdrant readiness, local Compose service/storage and CI integration.

Existing document state remains PARSED; **index READY is a separate state**, not
lease approval, malware clearance, full processing completion or an AI result.
Parsing and indexing are separately requested. No Redis enqueue, API background
processing, Qdrant-generated answer, graph entity, or inferred lease field is claimed.

## Embedding choices — important distinction

### Default: real offline lexical vectors

`LEASEGUARD_EMBEDDING_PROVIDER=hashing` uses normalized Unicode word counts,
SHA-256 signed feature hashing, log term frequency and L2 normalization. Default
size is 512. It needs no key, model download or external request. It produces real
vectors consumed by Qdrant; it is **not a neural semantic model**. Synonyms may
not match, hash collisions may cause irrelevant hits, and similarity is not legal
confidence. UI and API explicitly label this baseline. Retrieval quality has not
been benchmarked against a legal golden dataset (Phase 20).

### Optional: real remote semantic embeddings

```text
LEASEGUARD_EMBEDDING_PROVIDER=openai
LEASEGUARD_EMBEDDING_URL=https://api.openai.com/v1
LEASEGUARD_EMBEDDING_MODEL=text-embedding-3-small
LEASEGUARD_EMBEDDING_DIMENSIONS=512
# Set LEASEGUARD_EMBEDDING_API_KEY securely in your environment, never in Git/chat.
```

The adapter really calls `POST /embeddings` with model, dimensions, input and
float encoding. Compatible services must support that contract and indexed batch
responses. Batches are limited to 32, the response stream to 8 MiB and the overall
HTTP operation to 30 seconds. Shape, indexes, dimensions, numeric finiteness and
nonzero vectors are checked. Redirects/proxy environment inheritance are disabled;
errors expose only EMBEDDING_UNAVAILABLE, never response bodies or keys.

**Explicitly selecting this provider sends chunk text and search queries to that
configured service.** Use an approved data-handling endpoint and review cost,
residency/retention and consent. Do not send real sensitive leases in this
unfinished development platform. No external semantic call was executed here:
its HTTP contract and malformed-response handling are tested with an HTTP mock
transport, not claimed as a live provider integration. There is no fake successful
semantic response in the running application. LLM/provider orchestration remains
Phase 8 onward.

An embedding profile hashes provider, revision, dimensions and (for remote calls)
model/endpoint. Keys are not part of it. Collections use `leaseguard_<profile hash>`.
Restart API and all workers when changing configuration; explicitly index again
under the new profile. Old-profile results cannot silently mix with new query
embeddings. Wrong collection dimensions/distance fail with VECTOR_SCHEMA_MISMATCH;
existing collections are never automatically dropped/recreated.

## Durable indexing and failure semantics

```text
parsed latest version → explicit POST index → QUEUED → RUNNING → READY
                                                   └──────────→ FAILED
```

One SQL job/manifest per version. Scheduling commits before 202 is returned.
Repeated scheduling returns the existing active/ready job; 202 by itself does not
mean vectors are ready. Failed jobs require an explicit POST retry within the
attempt cap. `?rebuild=true` deliberately queues an already READY version again
(e.g. after restoring Qdrant); successful rebuilds start a fresh attempt budget.
Changing profile also starts a new budget. No automatic successful reindex loop.

- Document-then-job lock order matches other document operations; PostgreSQL
  `SKIP LOCKED` prevents two workers claiming the same eligible version.
- Each claim gets a random generation UUID and lease; each point ID is UUIDv5 of
  generation + chunk ID. Expired leases can be reclaimed, bounded by attempts.
- Workers check source slices, parser revision and complete parsing-job chunk
  counts before indexing; verify requester/org permissions when claiming and
  before publication. New-version/archive changes during work prevent publication.
- Qdrant writes use `wait=true`; only completed responses count. All batches must
  finish before one SQL commit marks the generation READY. Retrieval permits only
  READY generations for the active profile. Partial writes, crashed workers and
  expired generations are invisible even if their vectors exist physically.
- A stale worker can continue writing only its old generation; it cannot publish
  or overwrite the new generation. Failures store sanitized codes, no source text
  or service exception strings in job logs. SQL failure/uncertain commit does not
  turn an uncommitted manifest into a successful response.
- Defaults: 120-second operation deadline, 180-second lease, 3 attempts. Lease must
  exceed timeout by more than 30 seconds. No heartbeat, cancellation API, queue
  fairness/quotas or worker health/metrics are claimed. Slow I/O/commit can cause
  reclaim rather than success; synchronize clocks and supervise processes.

Old generations/collections are **retained**, not garbage-collected automatically.
They consume space and remain sensitive. Archiving hides data from retrieval but
is not physical erasure. Tenant deletion, orphan reconciliation, retention and
backup/restore testing require future hardening. Do not delete Qdrant volumes to
"fix" an index without acknowledging the loss and explicitly rebuilding manifests.
API readiness checks connectivity, not worker liveness or every point's presence.

## Tenant isolation and metadata

Qdrant is a derived index, never the authorization/source-of-truth database.

1. Active authentication/RBAC resolves organization from signed identity. Clients
   cannot select an organization; extra search fields such as organization_id fail
   validation. SUPER_ADMIN is not a tenant bypass.
2. PostgreSQL selects at most 200 eligible READY version generations within the
   authenticated organization. Current, non-archived versions are the default;
   historical versions need an explicit document ID plus version number.
3. Live `document_type` / `property_name` filters use user-supplied SQL metadata.
   Editing these labels does not require an index rebuild; stale payload snapshots
   cannot grant access or mis-filter returned results.
4. Qdrant queries always include **organization_id AND published generation IDs**,
   plus optional exact page/section. No unfiltered public adapter/search route exists.
5. Each hit must have the expected generation-derived point ID, profile, source
   version and tenant. SQL eligibility is rechecked **after** external vector I/O,
   including archive/new-version/metadata/publication changes.
6. Citation lookup is tenant-scoped; the chunk must match the source page slice,
   parser revision and payload source hash. Returned text comes from PostgreSQL,
   never a Qdrant payload. Forged cross-tenant IDs cannot leak evidence even if
   a vector store incorrectly returns them. Invalid hits are dropped fail-closed.

Point metadata includes organization_id, document_id, version_id, document_version,
chunk_id, generation, page, section hint, parser revision, source hash, embedding
profile, document_type and property. `tenant` and `clause_type` are explicitly null:
those domain/extraction entities do not exist yet. They cannot be filtered as if
extracted facts. Section headings remain Phase 5 heuristics, not legal classifications.
Metadata/vector representations may reveal sensitive information; they are **not
anonymization**. Development Qdrant is private on loopback/container networking.
Production retrieval settings require authenticated HTTPS Qdrant and HTTPS remote
embeddings, but production remains blocked by missing malware/security hardening.

## APIs

All routes below require authenticated document-reader permission, except indexing
requires writer permission (ORG_ADMIN/SUPER_ADMIN/ANALYST in their own organization).

| Method | Route | Result |
| --- | --- | --- |
| GET | `/api/v1/search/config` | Enabled flag and safe embedding/profile description |
| POST | `/api/v1/documents/{id}/versions/{n}/index` | Queue or explicit failed retry; `?rebuild=true` forces a READY rebuild |
| GET | same `/index` | Actual job or null |
| POST | `/api/v1/search` | Filtered top-k source evidence, not a generated answer |
| GET | `/api/v1/search/citations/{chunk_id}` | Rechecked source citation, independent of Qdrant availability |

Search body:

```json
{
  "query": "renewal notice period",
  "top_k": 5,
  "document_id": "<actual document UUID>",
  "version_number": 1,
  "page": 1,
  "section": "2. NOTICE",
  "document_type": "LEASE",
  "property_name": "Demo property"
}
```

Only `query` is required; all filters are optional except version_number requires
document_id. Omit unused values, not placeholders. Query max 2,000 characters,
top_k 1–20, exact metadata/heading matching. More than 200 eligible versions returns
422 and asks for narrower filters; no silently truncated tenant-wide search.
At most 80 candidates are overfetched; invalid hits/deduplication can yield fewer
than k. Only positive cosine matches are returned. Overlapping chunks can repeat
text. There is no reranker, query rewriting, hybrid/BM25 fusion or clause-absence
proof. No results means no matching eligible evidence, not "clause absent".

Each citation includes document/title, immutable version ID/number, chunk ID,
physical page OR logical segment label, section, exact `[start,end)` Unicode
code-point offsets into normalized page text, evidence quote, parser revision,
current user metadata and an authenticated original download API path. That URL
is not public/presigned; use the existing bearer-authenticated download flow.
Historical citations never silently point at the latest original. Archived sources
are excluded (404 on citation reads). Wrong-tenant object IDs return 404; unindexed
eligible scopes return a truthful empty result, while vector/provider outages
return 503 rather than fake empty success. Source corruption on direct citation
recheck returns 409. Sensitive responses have Cache-Control: no-store.

## Running

Fresh synthetic development:

```sh
python scripts/init_dev_env.py
docker compose up --build
```

Compose adds pinned Qdrant `v1.17.0`, a persistent `qdrant_data` volume, private
`127.0.0.1:6333` API port, and separate `index-worker` with init/restart/memory/PID
limits. Browser traffic still uses same-origin `/api`; no browser Qdrant/API key.

For an **existing** `.env`, preserve it and add explicitly:

```text
LEASEGUARD_RETRIEVAL_ENABLED=true
LEASEGUARD_QDRANT_URL=http://localhost:6333
LEASEGUARD_EMBEDDING_PROVIDER=hashing
LEASEGUARD_EMBEDDING_DIMENSIONS=512
```

Keep the Phase 5 processing/unscanned flags enabled **only for synthetic development**.
Code defaults retrieval off. The development example opts in; production rejects
the unscanned bypass, so current NOT_SCANNED versions cannot be newly indexed there.
Compose overrides Qdrant hostname to its internal service URL, not browser localhost.

Local Python 3.12+ after upgrading `.[dev]`, starting services and migrating:

```sh
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
# Separate terminals with identical settings and backend working directory:
python -m app.processing.worker
python -m app.search.worker
# Or one index queue pass:
python -m app.search.worker --once
```

Documents → upload synthetic fixture → Parse this version → wait for PARSED →
Index parsed source → wait for index READY → retrieve evidence. Use optional exact
page/heading filters; inspect source offsets and **Recheck citation**. Selecting an
older version uses its original citations if that version was indexed previously.
New uploads do not automatically index or replace existing source text.
If jobs remain queued, check flags and `docker compose logs index-worker`; API
health is not proof of a live worker. No LLM or GitHub token is required.

## Verification and limitations

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0005_retrieval:0004_processing --sql
# With real PostgreSQL/Redis/MinIO/Qdrant:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

Local verification: **87 backend tests passed, 6 live integration tests skipped;
16 frontend tests passed**. Ruff/format/mypy, TypeScript, frontend production
build and SQLite migration apply/model comparison/full reversal pass. PostgreSQL
migration SQL renders offline. Local Python is 3.11; Docker/CI target 3.12. No
Docker, PostgreSQL, Redis, MinIO or Qdrant server binary is available here.
Remote CI execution/success and live semantic-provider credentials are not claimed.

The retrieval tests run actual normalized sources, parsers, SQL operations and
**Qdrant's real in-process local engine**, not a mocked successful search service.
Tests cover two organizations, exact filters/source slices, idempotence, metadata
changes, current/history/archive behavior, reader/write RBAC, unknown filters,
partial-generation failure/retry/rebuild, stale leases/exhaustion, external outage
codes, forged tenant payloads, foreign chunks with spoofed valid tenant metadata,
source corruption and archive changes during external lookup. HTTP mock transport
checks the optional embedding service contract. UI tests cover honest embedding
labels, explicit scheduling, escaped evidence, offsets/logical labels, rechecks,
empty results and real errors. The existing Starlette/httpx deprecation warning
remains; no full browser end-to-end test is claimed.

CI now provisions real Qdrant alongside its existing integration services. The
opt-in server integration races two PostgreSQL index claimers, expires/reclaims a
lease, verifies stale ownership fencing, indexes two organizations and checks
retrieval/citation isolation. Local engine tests do not prove distributed/server
or PostgreSQL lock behavior; those integration cases are configured but skipped
here. Qdrant local-mode payload-index warnings are filtered only in local tests
because actual server indexes, not that local feature, matter in deployment.

Downgrade: stop API writes/index workers, back up and review first. Dropping
`0005_retrieval` deletes SQL index manifests and restores Phase 5 baseline but
**does not delete Qdrant collections/points or originals/pages/chunks**. Re-upgrade
requires explicit indexing to publish new manifests. External retention and
restoration must be handled deliberately; no destructive automatic cleanup exists.

## Changed areas and stopping point

New `search/{embeddings,models,store,worker,router}.py`, `0005_retrieval`, retrieval
HTTP/local-engine/server-integration tests; settings/app lifecycle/no-store/health;
metadata registration and migration tests; shared processing fixture and integration
cleanup; React retrieval panel/tests, processing integration and Phase 6 display;
Python dependencies, environment template, Compose, CI and architecture/runbooks.

Stopped after Phase 6. Next: **Phase 7 — Neo4j knowledge graph**. No graph entities,
relationships, LLM answers or extraction workflows were implemented ahead of approval.
