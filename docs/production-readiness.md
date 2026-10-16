# Production launch gates — NOT READY

This repository is an implemented MVP with safety boundaries, not a completed
production deployment. These gates are deliberately not auto-marked successful.

## Infrastructure and release
- Provision PostgreSQL/Redis/MinIO and enabled Qdrant/Neo4j; run all real integration
  variants and PostgreSQL lock/concurrency/rollback/immutability tests. Local SQLite
  operations and rendered PostgreSQL SQL are not substitutes.
- Apply migration head and confirm `/api/ready` is 200. Liveness 200 is insufficient.
- Build/scan actual Docker images with the Python3.12 lock; pin base/service images
  by approved digest (development MinIO still uses latest); stage before promotion.
- Use secret manager/KMS, TLS at the edge and between services, private dependency
  networks, restrictive runtime DB roles and bounded worker/container resources.
- Public registration must be disabled in production; settings reject enabling it.
  Complete MFA/SSO, verification, invitations/recovery and operational account lifecycle
  before exposing production identities. Never share credentials in chat or Git.
- Configure edge rate limits, distributed WebSocket/concurrency caps, WAF and proxy
  trust. Per-process socket limits and org provider quotas alone are not a global
  abuse/billing solution. Conduct penetration testing; add tenant RLS as defense in depth.
- Integrate malware clearance/quarantine. Production already rejects unscanned bypass;
  there is no implemented real scanner, so do not bypass policy to process real files.
- Validate browser CSP, websocket Host/Origin upgrades, disabled caching, downloads,
  XSS handling, key rotation and response/log redaction under the actual ingress.

## Correctness and identity
- Review is per field, not contract approval. Define separate legal authority,
  effective-date/supersession decisions, clause/entity alignment, multi-stage review,
  reviewer assignment/escalation, policy overrides and canonical lease publication
  before relying on contractual obligations or complete legal conflict detection.
- Have qualified reviewers curate representative independent labels and run actual
  permitted provider predictions, citation/extraction regressions and calibration.
  Synthetic unit/mock success is not a model-quality or legal-accuracy assessment.
- Preserve all historical source snapshots and review/audit data; no rollback should
  destroy populated ledgers to make a migration test pass.
- Tenant admins cannot provision platform SUPER_ADMIN; provision/monitor operational
  identities separately and test global-metrics authorization.

## Delivery, operations and billing
- Rehearse actual STARTTLS SMTP, public-IP-pinned HTTPS + signature verification,
  receiver timestamp/replay handling, backoff, lease expiry/crash and ambiguous sends.
  Complete email verification; introduce per-tenant endpoint/vault routing if required.
- Deploy/validate OTLP collector, Prometheus/Grafana, alerts, token rotation, incident
  dashboards and retention. Exporter configuration files do not demonstrate ingestion.
- Load-test parsing, extraction, database org/audit locks, live polling, queue pagination,
  outbox fairness and recovery. Current preference scheduling is bounded to 100 rows
  per cycle; do not claim enterprise-scale fairness without a sharding/cursor design.
- Complete priced usage reconciliation, plan enforcement, payment/invoice/subscription
  integration and legal billing/tax policies if commercial billing is required. Current
  meters intentionally leave unknown cost null and are not invoices.
- CRM/ERP adapters remain explicit mocks. Real integration mapping, credentials,
  idempotent remote updates, retries and customer data agreements require separate
  staging before enabling real outbound business-data synchronization.

## Backup/recovery rehearsal
- Use encrypted backup storage with restricted ACLs, retention and tested key recovery.
  `scripts/backup.py` creates private custom pg_dump archives and a checksum manifest;
  it does **not** encrypt files, prove a restore or coordinate all dependencies.
- Quiesce writes or define tested PITR consistency across DB, immutable originals,
  object versions, graph/vector metadata and external audit anchors. Document RPO/RTO.
- Restore into a new empty isolated target with the explicit restore flag; verify
  schema readiness, full audit anchors, original checksums, review/source links,
  credentials/tenant boundaries and representative workflow reads before promotion.
- Qdrant/Neo4j are derived but must be rebuilt or snapshot/restored consistently with
  SQL publication metadata. Redis provider budget/lease loss needs a reviewed recovery
  policy; never blindly replay an unknown paid request.
- CI empty-database downgrade is a schema rehearsal, not disaster recovery.

No real PostgreSQL deployment, paid model call, SMTP/webhook send, Docker deployment,
backup restore, legal validation or production penetration/load test was claimed in
this sandbox. Record external evidence and ownership for each gate before launch.
