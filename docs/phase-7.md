# Phase 7 — source-backed Neo4j knowledge graph

## Scope, inspection and plan

Phase 6 had parsed immutable versions/chunks, citations and Qdrant, but no graph
entities, relationships or Neo4j client. The supplied Phase 7 requires tenants,
landlords, leases, properties, clauses and relationships. Automated extraction is
Phase 10 and approval is Phase 13; this phase must not invent those results.

Implemented plan: typed assertion/evidence schema → immutable PostgreSQL revisions
and durable projection manifests → bounded Neo4j adapter → leased worker → scoped
APIs and neighborhood traversal → minimal real UI → tests and operations docs.

**This graph contains manual, HUMAN_ASSERTED_UNVERIFIED proposals.** A source citation
proves linkage to original text, not that a proposed party role, clause type or
relationship is semantically/legalistically correct. No AI extraction, inference,
confidence, approval or complete audit system is claimed. There is no production
or test-data fallback graph in the running application.

## Entities and relationships

Real Neo4j typed labels share `LeaseGuardEntity` and one allowed entity label:
Tenant, Landlord, Lease, Property, Clause, Amendment, RenewalOption, Obligation.
The last three prepare the additional graph relationships in the specification;
they are still manual assertions, not implemented extraction schemas/rules.

| Directed relationship | Source | Target |
| --- | --- | --- |
| HAS_LEASE | Tenant | Lease |
| LANDLORD_OF | Landlord | Lease |
| OWNS | Landlord | Property |
| LOCATED_AT | Lease | Property |
| HAS_CLAUSE | Lease | Clause |
| HAS_AMENDMENT | Lease | Amendment |
| HAS_RENEWAL_OPTION | Lease | RenewalOption |
| HAS_OBLIGATION | Lease | Obligation |
| APPLIES_TO | Obligation | Tenant |

At least one Lease node is required; at most 100 nodes and 200 relationships per
snapshot. Local node/edge IDs match `[a-z][a-z0-9_]{0,39}` and are unique within
their respective lists. Directions/types and endpoint existence are validated;
duplicate same-type/source/target edges are rejected. Names are 1–200 characters,
case-sensitive exact substrings of a cited normalized source chunk. This prevents
invented names but **does not validate the entity type or relationship meaning**.
Every node and edge needs 1–5 distinct actual chunk UUIDs from the same organization,
document and immutable version. All source slices and parser revisions must match.
A meaningful rationale (10–1,000 characters) records the proposer's explanation.
Extra fields, including organization_id, confidence or verified, are rejected.

Commercial Tenant entities are not security tenants: `organization_id` is always
the trusted security boundary. Equal names/IDs never cause automatic identity
resolution or merging across documents, revisions or organizations. Cross-document
entity resolution, relationship inference and lease-domain extraction remain future
work. Cycles are allowed only through valid relation types; traversal is bounded.

## Persistence, provenance and projection

`0006_graph` creates `document_graph_snapshots` with composite tenant/version and
creator/requester foreign keys, unique `(version_id, revision)`, immutable JSON
assertions, canonical content hash, source chunk hashes, creator/timestamp, and a
projection job state. PostgreSQL is authoritative; Neo4j is a derived projection.
Assertions are never overwritten by editing a snapshot. A new revision needs
`expected_revision` optimistic concurrency under the document lock. A lost/uncertain
response is reconciled by reading the latest revision, not blindly appending again.

```text
explicit manual proposal + valid evidence → QUEUED → RUNNING → READY
                                                       └────→ FAILED
```

READY means only that the graph projection was published, **not verified/approved**.
The document itself stays PARSED. The latest graph revision may be pending/failed;
it does not silently fall back to an earlier successful graph. Earlier READY
revisions require explicit history selection. Old document versions can be read
explicitly, but only the latest parsed, non-archived document version accepts new
proposals/retries/rebuilds. Archive excludes active graph reads, not physical erasure.

The separate `python -m app.graph.worker` polls SQL (not an in-memory API task):

- Document-then-snapshot lock order, PostgreSQL SKIP LOCKED, finite leases and max
  attempts. Revoked/inactive requesters/orgs, scan policy, changed source versions
  and superseded pending revisions fail closed. Authorization is checked again
  before publication. Creator remains immutable when another writer retries.
- Each claim has a fresh generation UUID, acting as its fencing token. Neo4j
  entities/edges/projection marker include org/document/version/snapshot/generation
  scope. Hashed keys are globally unique per scope; a stale generation cannot
  mutate the new generation's nodes/relationships.
- Parameterized Neo4j transactions atomically write all typed nodes, relationships
  and a projection marker. Only labels/relationship types from server-owned enums
  appear in query text; user names/evidence IDs are bound parameters. No arbitrary
  Cypher, URI, database name or graph scope is accepted by an HTTP request.
- One SQL commit publishes READY **after** Neo4j commits and source hashes/policy
  are rechecked. Failed/abandoned/partially written generations remain invisible.
  External and SQL commits are not a distributed transaction or exactly-once claim.
- Expired ownership cannot publish; expired leases recover within the cap. Old
  superseded QUEUED/RUNNING revisions become FAILED/SUPERSEDED when polled. A
  subsequent worker poll may be needed to drain old pending candidates.
- Explicit retry handles FAILED latest revisions; `?rebuild=true` reconstructs a
  READY projection after external loss. Rebuild temporarily hides that revision
  until its new generation is published. Failed attempts are capped; successful
  rebuilds start a new budget. Other successful/history revisions are not erased.

Defaults: 60-second graph operation deadline, 120-second lease, 3 attempts; lease
must exceed timeout by more than 30 seconds. Neo4j connection/acquisition/retry
settings are finite, projection transactions use 30 seconds, read transactions 15,
API graph I/O 20. Source-count bounds limit writes/reads/traversal. No worker
heartbeat, cancellation, tenant quotas/fair scheduling or automatic graph garbage
collection exists yet. Slow publication can lose its lease; synchronize clocks
and supervise processes. API readiness is not proof that a worker is running.

## Authorized graph reads and provenance

A graph read first authorizes the document/version and READY SQL snapshot. It
revalidates all source hashes before and after external I/O. Neo4j reads bind the
full organization/document/version/snapshot/generation scope. Results are compared
against canonical SQL node/relationship sets and hash; forged, missing, duplicated
or changed projection data cannot become a successful graph response. Returned
labels/evidence IDs come from the verified SQL proposal, after checking Neo4j.
No raw foreign graph properties or arbitrary query results are exposed.

After I/O, SQL publication, current graph revision (unless historical selection)
and archive state are checked again. A newer pending proposal cannot make a stale
graph appear current. Source corruption or projection mismatch returns 409;
Neo4j outage/missing published generation returns 503, never a successful empty
or SQL-only fallback graph. Wrong-tenant IDs return 404, including evidence IDs.
Graph readers use document-reader RBAC; writers use document-writer RBAC. Signed
identity determines organization; SUPER_ADMIN is not a cross-tenant bypass.

Optional `root_id` and 1–3 `hops` perform an **undirected neighborhood traversal**
over the bounded, retrieved and SQL-revalidated Neo4j graph. Output edges retain
their real direction/type. There is no unbounded Cypher path query, inferred edge
or cross-source traversal. Exact evidence is rechecked through the Phase 6
citation API with original version, physical page or logical segment and Unicode
source offsets. A SQL proposal/history read is retained manual input, not evidence
that Neo4j is available or that all underlying source currently passes validation.

## APIs

Version base: `/api/v1/documents/{document_id}/versions/{version_number}`.

| Method | Route | Meaning |
| --- | --- | --- |
| GET | `/api/v1/graph/config` | Safe enabled flag, allowed schema, limits; no connection credentials |
| GET | `{base}/graph/snapshot` | Latest SQL proposal/job or null |
| GET | `{base}/graph/snapshots?limit=5&offset=0` | Immutable proposal history; max 20/page |
| POST | `{base}/graph/snapshots` | New source-backed manual revision + durable projection job (202) |
| POST | `{base}/graph/snapshots/{revision}/project` | Explicit retry/idempotent scheduling; `?rebuild=true` rebuilds READY |
| GET | `{base}/graph` | Actual, integrity-checked READY Neo4j projection |
| GET | `{base}/graph?revision=1&root_id=tenant&hops=2` | Explicit historical revision / bounded neighborhood |

202 means a durable request, not completed or verified facts. Sensitive responses
are no-store. Source/citation reads use existing bearer auth, not public URLs.

Example JSON for the fictional `evaluation/fixtures/synthetic-graph-source.txt`;
replace `<actual chunk UUID>` using the real chunk catalog after parsing. This is
an example proposal format, not an automatically populated result:

```json
{
  "expected_revision": 0,
  "nodes": [
    {"id":"lease","kind":"Lease","name":"Lease Alpha","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"tenant","kind":"Tenant","name":"Tenant Beta","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"landlord","kind":"Landlord","name":"Landlord Gamma","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"property","kind":"Property","name":"Property Delta","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"clause","kind":"Clause","name":"Clause Rent","evidence_chunk_ids":["<actual chunk UUID>"]}
  ],
  "edges": [
    {"id":"occupancy","kind":"HAS_LEASE","source":"tenant","target":"lease","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"location","kind":"LOCATED_AT","source":"lease","target":"property","evidence_chunk_ids":["<actual chunk UUID>"]},
    {"id":"rent","kind":"HAS_CLAUSE","source":"lease","target":"clause","evidence_chunk_ids":["<actual chunk UUID>"]}
  ],
  "rationale":"Manual synthetic test proposal; entity roles and relationships are not verified."
}
```

The UI sets expected_revision automatically from its latest loaded snapshot. Paste
only nodes/edges/rationale into the UI editor. The API needs expected_revision too.
Using placeholder UUIDs or names absent from source correctly fails validation.

## UI

Documents → select a parsed source/version → Knowledge graph panel:

- Inspect real source chunk IDs/text; five-chunk pagination avoids loading all text.
- Enter a manual JSON proposal; inspect allowed types/directions and copy the
  latest proposal to deliberately create a new revision. Nothing is auto-proposed.
- Poll actual projection state every two seconds while QUEUED/RUNNING; explicitly
  retry/rebuild. Pending acceptance is never presented as a completed graph.
- Inspect Neo4j nodes and directed relationship lists, select neighborhood root/
  hop limit, inspect the newest 20 proposal revisions (API history can paginate
  further) and fetch exact cited source. These are accessible cards/lists, not a
  force-directed visualization or reasoning agent.
- Labels, rationale and source are React-escaped, never inserted as HTML. Every
  view carries UNVERIFIED warnings. Viewers can inspect but not propose or rebuild.

## Run / configuration

Fresh synthetic-development stack:

```sh
python scripts/init_dev_env.py
docker compose up --build
```

The helper never overwrites an existing `.env`; it generates a random matching
Neo4j password in the two template locations without printing it. Compose adds
`neo4j:5.26.16-community`, persistent `neo4j_data`, loopback ports 7474/7687, a
Cypher health check and a separate `graph-worker`. API/worker use internal
`bolt://neo4j:7687`; browser calls remain relative `/api` and receive no Neo4j key.

For existing environments, explicitly enable graph and configure matching passwords
securely in `.env`; do not paste real credentials into chat or commit them:

```text
LEASEGUARD_GRAPH_ENABLED=true
LEASEGUARD_NEO4J_URI=bolt://localhost:7687
LEASEGUARD_NEO4J_USERNAME=neo4j
LEASEGUARD_NEO4J_DATABASE=neo4j
# Set NEO4J_PASSWORD and LEASEGUARD_NEO4J_PASSWORD to the same new dev password.
```

Code defaults are off. Enabling graph requires an explicit password; `.env.example`
opts in for synthetic development. Compose overrides the internal URI/password
using its service hostname and NEO4J_PASSWORD. Existing Neo4j volumes keep their
existing database password: changing environment alone is not rotation. Never
silently delete/reset a volume to fix a credential mismatch.

Local Python 3.12+, after installing updated `.[dev]` and starting dependencies:

```sh
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
# Separate terminals, identical configuration and backend working directory:
python -m app.processing.worker
python -m app.graph.worker
# Or a single graph queue pass:
python -m app.graph.worker --once
```

Parsing/graph changes still obey the unscanned-source gate from Phase 5. Production
refuses that bypass; current uploads remain NOT_SCANNED and cannot be projected
there. Production graph configuration also requires certificate-verified `bolt+s`
or `neo4j+s`, explicit authentication, and reviewed database/network access. Worker
currently provisions fixed constraints/indexes and needs schema permissions; split
schema provisioning from runtime privileges during hardening. The default dev
Neo4j account and images/settings are not a production security policy.

Neo4j stores names/relationships/evidence identifiers, so it is sensitive, not an
anonymized store. Old revisions/generations persist. Archive/downgrade do not erase
them. Retention, deletion, orphan reconciliation, backup/restore, fine-grained DB
roles, usage quotas and full operational audit are still future work. No LLM,
GitHub token or external AI provider key is needed for this phase.

## Verification and limits

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0006_graph:0005_retrieval --sql
# Real migrated PostgreSQL/Redis/MinIO/Qdrant/Neo4j:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
```

Local results: **100 backend tests passed, 7 live integration tests skipped; 20
frontend tests passed**. Ruff/format/mypy, TypeScript and production build pass.
SQLite applies all migrations, matches models and fully reverses them; PostgreSQL
SQL renders offline. Local Python is 3.11 versus the target/CI/Docker 3.12.

**There is no Docker, Java/Neo4j server, PostgreSQL, Redis or MinIO here.** The graph
API/worker tests use an explicitly test-only contract double, and the real driver
adapter is checked with mocked transport/transaction objects. These prove local
lifecycle/validation/parameterization contracts, **not that Neo4j executed Cypher**.
No runtime mock graph is registered. Live server compatibility, constraint creation,
Neo4j transaction rollback, PostgreSQL row locks and container startup are unverified
locally. Remote CI success is not claimed. Existing Qdrant local-engine tests still
run; that is not a Neo4j substitute. Starlette's httpx deprecation warning remains;
no full browser end-to-end run is claimed.

Graph tests cover manual proposal concurrency tokens, immutable revisions, actual
source hashes/names/offsets, tenant and source-version ownership, extra/invalid
schema rejection, viewer RBAC, scan/disabled policy, queued-versus-ready distinction,
retry/rebuild, superseded pending work, stale generation fencing, exhausted leases,
revoked proposers, timeout, source/projection corruption, outage errors and archive
changes during external reads. UI tests cover no invented graph, unverified queue
acceptance, escaped projection names, source evidence and outage behavior.

CI now adds real Neo4j and a dedicated opt-in server integration. That test races
PostgreSQL graph revision writers and claimers, expires/reclaims a lease, projects
the same named/local-ID entities into two organizations, reads actual Neo4j nodes/
relationships/neighborhoods, verifies no cross-tenant reads and checks ten separate
entities exist. These cases are configured and skipped here, not reported as run.

**Rollback:** stop API writes and graph workers; back up/review first. Downgrading
0006_graph drops SQL proposals/manifests and resets baseline to Phase 6, but leaves
Neo4j projections, originals, parsed pages/chunks and Qdrant unchanged. Never call
this physical erasure. Re-upgrade needs explicit new proposals/projection manifests.

## Changed areas / stopping point

New `backend/app/graph/` schemas/models/evidence service/Neo4j store/worker/API;
`0006_graph`, metadata registration/readiness/settings/lifecycle/no-store integration;
SQL/driver/API and real-server integration test definitions; existing cleanup paths;
React graph panel/tests and phase display; Python dependency, environment/password
helper, Compose/CI services, fixture and architecture/runbooks.

Stopped after Phase 7. **Next: Phase 8 — LLM provider abstraction and one working
provider**, building on the existing explicit embedding adapter. No LLM is connected
and no generated lease result is fabricated in this phase.
