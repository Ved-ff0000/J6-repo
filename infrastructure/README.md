# Infrastructure

Root `compose.yaml` starts PostgreSQL 16, Redis 7, MinIO, Qdrant and Neo4j. Separate one-shot
services provision the document bucket and run Alembic. Backend startup waits for
migrations, Redis health and bucket initialization; frontend waits for readiness.
PostgreSQL, MinIO, Qdrant and Neo4j have persistent volumes. Service ports (including MinIO API
9000 / console 9001) are loopback-bound; frontend is exposed for development preview.

Compose 2.24+ optionally injects root `.env`. The non-overwriting local helper
`scripts/init_dev_env.py` generates signing/storage keys without printing them.
Upgrading configurations must add Phase 4 settings explicitly. Demo defaults/root
MinIO credentials and the `latest` MinIO image tag are not production policy;
pin/verify deployment images and dedicated IAM, TLS, encryption and backups first.
Existing volumes are not migrated by changing environment variables/backends.

See [document storage operations](../docs/phase-4.md),
[identity operations](../docs/phase-3.md) and the [foundation](../docs/phase-2.md).
Docker/MinIO startup has not been verified in the sandbox. CI is configured for
live PostgreSQL/Redis/MinIO round trips and concurrent version tests; remote success
is not claimed. Qdrant is now configured and locally tested through its in-process
engine. Neo4j is configured in Phase 7 but its server is not locally available.

Phase 5 adds a separate bounded `worker` process with a PostgreSQL durable queue,
finite leases and fenced result publication. Add the two explicit synthetic-only
processing flags from `.env.example` when upgrading. API readiness does not verify
worker liveness; queued jobs wait when workers are stopped. See [Phase 5](../docs/phase-5.md).

Phase 6 adds private, persistent Qdrant (`v1.17.0`) and `index-worker`. API/worker
use `http://qdrant:6333` inside Compose; local Python uses loopback. Retrieval is
disabled in code defaults and explicitly enabled by the development example.
Qdrant readiness is checked only when enabled; ready API does not prove indexing
worker liveness or every point's presence. Never treat archive as vector erasure.
See [Phase 6](../docs/phase-6.md) for generations, rebuild and optional provider egress.

Phase 7 adds authenticated Neo4j (`5.26.16-community`), loopback 7474/7687 and
`graph-worker`. API/worker use the internal Bolt hostname; browsers receive no
graph credentials. The environment helper generates a matching Neo4j password for
fresh dev setups; existing volumes retain their password until explicitly rotated.
The worker provisions fixed constraints/indexes and currently needs schema privileges.
Graph readiness is conditional and does not prove worker liveness/projection integrity.
Immutable unverified SQL assertions are not approvals. Archive/downgrade do not erase
Neo4j data. See [Phase 7](../docs/phase-7.md) for security and operations limits.

Phase 8 needs no new container or model download. The existing API/env-file path
accepts the optional server-only `LEASEGUARD_LLM_*` settings; generation stays off
by default and credentials are never sent to the browser. Apply migration
`0007_providers` before starting the new API; readiness requires schema `phase8`.
LLM probes are explicit, potentially paid administrator actions, not automatic
health/readiness requests. Redis admission and the SQL idempotency ledger must
remain available; an interrupted STARTED attempt must be reconciled, not silently
retried with a new key. Normal CI never calls a paid model. See
[Phase 8](../docs/phase-8.md) for safe configuration and opt-in verification.

Phase 9 adds the LangGraph runtime dependency and `0008_workflows`; reinstall the
backend dependencies, migrate and restart. Readiness requires schema `phase9`.
`LEASEGUARD_WORKFLOW_ENABLED` defaults false; enabling it also requires approved
LLM/retrieval configuration and per-request document-egress consent. The graph runs
as bounded asynchronous I/O inside the HTTP request, not a new worker or a resumable
checkpoint queue. Two generation units are reserved from the diagnostic's existing
Redis budget, with a shared owned lease. Persist/backup/reconcile the SQL run ledger
before retrying unknown paid outcomes; no automatic resume occurs. No hosted
LangGraph/LangSmith service or key is required. See [Phase 9](../docs/phase-9.md).

Phase 10 adds no service/dependency. Apply `0009_lease_extractions` and restart;
readiness requires schema `phase10`. New `LEASEGUARD_LEASE_EXTRACTION_ENABLED`
defaults false independently of candidate workflows and requires at least 1024
configured output tokens (2048 recommended). Existing consent, source scan policy,
shared Redis budget and no-automatic-resume behavior remain. Migration preserves
old JSON/revisions as CANDIDATES and refuses downgrade while any LEASE_FIELDS runs
exist; retain/reconcile/export before a destructive rollback. See
[Phase 10](../docs/phase-10.md) for privacy, budget and completeness caveats.


## Phase 11 deterministic-rule boundary

Versioned local checks now annotate new explicitly rule-enabled lease workflows and
can separately re-evaluate stored eligible immutable extractions through the admin
API/UI. Policy/input hashes, complete rule versions and findings are retained; this
makes no legal-compliance, accuracy, completeness or approval claim. Unobserved
notice on partial input is indeterminate. Rules are off by default and add no model
calls; paid extraction still requires every existing gate. Migration 0010_rules
requires phase11 readiness and refuses destructive rollback when rule evaluations
or rules-enabled runs exist. SQL ledger append-only API is not a privileged-SQL-proof
audit trail. See [Phase 11](../docs/phase-11.md) for the catalog, configuration,
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
infrastructure/paid-provider validation is still gated. See [Phase 12](../docs/phase-12.md).

## Stages 13–24 implementation update

Schema head is `0013_operations`; baseline phase24 denotes implementation only.
Human reviews, audit guards, connected workspace, opt-in live notifications, analytics,
explicit mock adapters, usage, evaluation, observability and backup tooling are now
present. Docker includes an operations-worker; runtime dependencies are hash-locked.
Deployment, live transports, actual PostgreSQL recovery/concurrency and legal-quality
validation remain unverified. See [status](../docs/completion-status.md), [operations](../docs/governed-operations.md)
and [production gates](../docs/production-readiness.md).
