# Governed workspace operations — stages 13–24

## Review and audit

New migrations: `0012_governance`, `0013_operations`; readiness baseline is phase24.
Apply `alembic upgrade head` before new routes/workers. Existing histories remain
unchanged. Migration 0012 includes audit-table immutability triggers; 0012/0013 refuse
rollback with retained governance/delivery/integration records. Earlier guarded
rollback contracts remain. ORM-only unit fixtures do not install migration triggers;
a separate migration test executes and verifies the SQLite immutability triggers.

Set `LEASEGUARD_REVIEW_AUTO_ENQUEUE=true` for automatic field tasks on NEW successful
lease-mode workflows. The Python default is off for backwards compatibility;
`.env.example` opts in while all paid execution remains off. Admins can backfill old
results via `POST /api/v1/workflow-runs/{id}/reviews`; each source field has one task.
Historical workflow request replay never silently re-extracts or upgrades old JSON.

Roles:
- Org/platform admins: review, audit and existing AI administration.
- REVIEWER: review/evidence/intelligence and field decisions, not audit/AI execution.
- AUDITOR: read reviews/evidence/audit, not field decisions.
- ANALYST/VIEWER: existing document/analytics access; no review decisions or audit.
- Operational Prometheus metrics: SUPER_ADMIN only, with the metrics flag enabled.
  SUPER_ADMIN remains scoped to its own org for business-data APIs; metrics are an
  explicit platform operational capability and contain no tenant ID/value labels.

`GET /api/v1/reviews?status=OPEN&limit=50&offset=0`, individual task and
`/reviews/{id}/decision` reads expose immutable source snapshots to permitted roles.
`POST /reviews/{id}/decision` accepts only request UUID, expected revision, action
ACCEPT/MODIFY/REJECT, and a trimmed 8–2000 character reason. MODIFY also requires a
literal value (<=300), quote (<=500) and source chunk UUID. The server recomputes
exact evidence and normalization; it never trusts arbitrary caller text as source.
Source must still be the latest PARSED unchanged nonarchived version, with recorded
checksum, field hash, SQL citation and fresh reviewer/session authority. New versions
or mutable metadata can make old tasks stale; they are retained, not silently remapped.

A task is finalized once. Decision records retain original/reviewed field JSON,
reviewer, reason, time and request fingerprint. Retry the original request UUID/body
on uncertain commits. Other/reversed inputs with the same UUID conflict. Different
UUID after finalization conflicts. Decisions do not rewrite workflow, rule, conflict
or canonical lease facts and do not signify whole-lease approval. Reject leaves a
null reviewed value; accept preserves the original proposal; modify records a fresh
source-linked proposal. Original model status/confidence are not relabelled as model
accuracy. Modifications do not imply a newly passed compliance-rule report.

Audit chain appends lock the org row and are committed with the business write.
Audit events retain opaque actor/resource IDs even if those identities are later
removed; no FK cascade silently erases audit history. Export/retain chain anchors
externally. `/api/v1/audit?after=0&limit=50` is sequence-paginated;
`/api/v1/audit/verify?after=0&limit=500` verifies a bounded window and supplies the
next anchor and `more` flag. Verification alone cannot detect truncating the final
rows or recomputing every digest with privileged DB access. Use least-privilege
runtime roles, backups and external anchoring. Denied requests and interrupted jobs
also have operational request/job logs; the audit is not represented as a complete
forensic capture of every subsystem side effect.

## Live updates

Enable `LEASEGUARD_LIVE_ENABLED=true`. Authenticated
`POST /api/v1/events/ticket` issues a single-use, hashed-at-rest 30s ticket; at most
three unused unexpired tickets per user. Connect same-origin
`/api/v1/events/live`, then send `{"ticket":"...","after":0}` as the first message
within five seconds. Never place JWTs/tickets in query strings. Tickets are consumed
atomically. Org/user/session is checked every two seconds; invalidation messages
contain only type/cursor, not private audit payloads. Sockets rotate after 15 minutes.
Vite/Nginx preserve Host and support upgrades; browser code uses the preview origin,
not localhost. A browser reconnect uses a new HTTP-authenticated ticket and the last
cursor. Session failures/dependency errors close, not spoof success. The browser
coalesces invalidations and supports manual refresh when live is off/unavailable.
Limits are per process; deployment needs global edge limits and load validation.

## Notifications, transports and reminders

`GET /notifications`, `GET/PUT /notifications/preferences`,
`POST /notifications/{event_id}/read`, `GET /notifications/deliveries` are scoped to
the trusted organization/current user. In-app notifications are org-operational event
metadata (IDs/type), not raw review evidence or audit reasons. Read receipts and
preferences are per user. Default in-app on; email/webhook off. Opting in to a
server-disabled transport rejects, rather than promising delivery. A newly created
preference starts its delivery cursor at current history, avoiding old-message floods.

Run `python -m app.operations.worker` (Compose includes operations-worker). It scans
bounded batches, emits at most one daily reminder per eligible human-reviewed literal
expiration within 30 days, schedules preference-selected events, cleans old tickets,
and claims deliveries with token/lease ownership. Latest nonarchived PARSED sources
only qualify for date reminders. This is not a full contractual deadline engine.
Email uses server-configured STARTTLS SMTP with normal certificate checks. Sender/
server must be configured and email explicitly enabled; username/password stay server
side. Account email verification is not implemented: keep real-data email off until
identity controls are completed.

Webhook transport requires `LEASEGUARD_WEBHOOK_ENABLED=true`, a server-owned public
HTTPS URL on port443, and a >=32-character signing key. No client-selected URL is
accepted. DNS answers must all be global/public; connection pins a checked IP with
original Host/SNI and certificate validation; redirects and proxy environment trust
are disabled. It sends canonical JSON event metadata, not quoted leases. Headers:
`X-LeaseGuard-Timestamp`, `X-LeaseGuard-Signature: v1=<HMAC-SHA256(timestamp.body)>`,
and stable `Idempotency-Key`/`delivery_id`. Receivers must verify signature with
constant-time comparison, timestamp freshness and delivery-ID deduplication. Retry
uses the same delivery ID and a fresh timestamp. The single configured endpoint/key
is an operator-controlled central receiver, not a self-service per-tenant endpoint
registry; introduce per-tenant vault-managed routing before external multi-customer
integrations.

Five attempts, exponential backoff, leased claims, safe fixed error codes and logs
are implemented. Worker crash after remote success can cause a duplicate; ambiguous
last-attempt crashes can leave a SENDING record for operator reconciliation. No
exactly-once guarantee or false SENT state. A disabled channel does not prevent a
different enabled channel from being claimed. In-flight messages cannot be unsent
on later revocation. No real SMTP/HTTPS delivery has been demonstrated locally.

## Analytics, integrations, usage and evaluation

`/analytics/summary` uses org SQL counts; acceptance rate is not AI accuracy. Accuracy
and calibrated confidence remain null. `/analytics/deadlines` returns bounded recent
human-reviewed literal dates from latest sources, explicitly not all legal deadlines.

`POST /integrations` accepts request UUID, human decision UUID and one of
salesforce_mock/sap_mock/servicenow_mock. Only visible accepted/modified decisions
qualify; results are SIMULATED with `external_request_made:false`, a record digest
and a warning. No real ERP/CRM records change. Generic webhook is the separate signed
notification transport, not a pretend successful Salesforce implementation.

`/billing/usage` returns retained versions/bytes, provider-reported diagnostic usage
and separate latest-1000 workflow usage. Missing token usage is counted as unknown;
actual cost and currency remain null. No charge, subscription, tax, invoice,
price estimate or reconciliation is invented.

Offline evaluation:

```sh
cd backend
python -m app.evaluation --gold ../evaluation/golden.json
python -m app.evaluation --gold ../evaluation/golden.json --predictions /private/predictions.json
```

First command validates eight synthetic labels and explicitly says no predictions
were scored. Second requires caller-supplied predictions; it never calls a model.
Exact field and source-span metrics penalize duplicate/missing predictions. Brier/
reliability diagnostics require >=20 supplied confidence samples and remain labelled
exact-match diagnostics, not deployed confidence calibration. The tiny synthetic
corpus is not production legal evidence. Do not commit real leases or predictions.

## Observability and deployment

`LEASEGUARD_METRICS_ENABLED` enables operator-authenticated
`/api/v1/observability/metrics`. Prometheus labels are method/template/status, never
raw path IDs, tenants, quotes, tokens or query strings. Use an operator-controlled
SUPER_ADMIN credential, rotate its short-lived bearer token in the mounted scrape
credential file and keep the service private. Never put that credential in a browser.
`LEASEGUARD_OTEL_ENABLED` opt-in exports metadata-only HTTP spans to the configured
collector; no source text, prompts, auth headers or tenant IDs are attached. Span
start/end reflect measured request latency. Disabled export has no remote exporter.
Collector, Prometheus and Grafana-ready example files are under
`infrastructure/observability/`; no live observability deployment is claimed.

Docker uses a hash-locked Python3.12 runtime dependency file. CI installs that lock,
runs unit/infrastructure tests with provider mocks, checks migrations, and performs
rollback only in a **fresh empty database**, never by deleting populated audit/history.
`python scripts/backup.py --help` describes no-clobber backup, checksum/catalog verify
and explicitly acknowledged restore into an empty target. Connection secrets are
environment-only, not command arguments or printed errors. Use encrypted storage;
the tool does not itself encrypt archives or replace a restore rehearsal. Source
object storage, vector/graph snapshots, encryption keys and external audit anchors
need their own coordinated backup/retention plan. See production readiness gates.
