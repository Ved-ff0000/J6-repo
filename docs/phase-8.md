# Phase 8 — provider abstraction and real OpenAI-compatible adapter

## Scope and inspection

Phase 7 contained real parsing, retrieval and manual unverified graph assertions,
but no generation provider or executing AI workflow. This phase introduces the
swappable provider contracts and **one actual HTTP adapter**, not a fake model or
a general-purpose chat UI. LangGraph/extraction remain Phases 9–10 and require
separate authorization. No document, graph assertion, review or approval is changed
by a provider diagnostic.

Implemented order: inspect existing embedding integration/settings → typed
interfaces and bounded transport → OpenAI-compatible text/structured generation →
preserve embedding profile compatibility → credential/permission/paid-egress gates
→ durable synthetic diagnostic ledger and scoped API → minimal UI/tests/docs.

## Interfaces and actual adapter

- `app.ai.interfaces.LLMProvider`: asynchronous `generate` and
  `generate_structured(request, PydanticModel)` with typed immutable inputs/results.
- `EmbeddingProvider`: asynchronous `embed`, profile/collection/dimensions and a
  truthful label. Hashing and OpenAI-compatible implementations live in `app.ai`.
  `app.search.embeddings.Embedder` is a compatibility facade; the exact Phase 6
  `embedding-v1` profile, collection identifiers and lexical algorithm are preserved.
  This refactor does **not** require a reindex or claim neural quality for hashing.
- `OpenAIProvider` makes a real `/chat/completions` HTTP request to the operator's
  server-configured endpoint using `max_completion_tokens`, `temperature`,
  `stream:false`, `n:1`, `store:false`. Structured calls use strict `json_schema`.
  Default model is `gpt-4.1-mini`. Compatible endpoints/models must support these
  fields and schema features; adapters for Anthropic/local model APIs are not faked.
- Structured output is checked twice locally: normalized closed JSON Schema
  (all fields required, optional values nullable, local references only) and strict
  Pydantic validation. Invalid JSON, duplicate keys, nonfinite numbers, schema/type
  errors, refusal, tool/function output, multiple choices and truncation cannot
  become successful results. Unsupported open dictionary schemas fail before egress.
- Generation input: at most 20 messages / 32,000 total characters, one leading
  system message, temperature 0–2, output 1–2,048 tokens bounded by server cap.
  HTTP response limit 1 MiB; generated content limit 32,000 characters. Embedding
  batches remain at most 32 texts / 8,000 characters each, with dimension/index/
  numeric-finiteness checks. Transport bounds elapsed I/O, streams finite chunks,
  does not follow redirects, use environment proxies, or automatically retry.
  It requests identity encoding and rejects compressed responses before decoding
  to avoid unbounded decompression.
- No tool execution, web search, caller-selected credentials/URLs/models, automatic
  provider fallback, downloads or fabricated responses exist. `store:false` is a
  request option, **not** a guarantee of a third party's retention/privacy policy.

## Controlled diagnostic and usage metadata

Only `ORG_ADMIN` / `SUPER_ADMIN` **within their own organization** have `ai:manage`:

| Endpoint | Behavior |
| --- | --- |
| `GET /api/v1/ai/config` | Safe server capabilities, gates, model/profile, embedding label and limits; no URLs/keys, no network probe |
| `POST /api/v1/ai/probes` | Fixed synthetic request; accepts only UUID `request_key` and strict boolean `acknowledge_external_request:true` |
| `GET /api/v1/ai/probes?limit=20&offset=0` | Tenant-scoped newest history, limit 1–50 |
| `GET /api/v1/ai/probes/{id}` | Same-tenant record or opaque 404 |

The versioned `provider_diagnostic` prompt is `1.0.0`, authored 2026-10-16,
with description, fixed templates and no user variables in `app.ai.prompts`.
It requests only `LEASEGUARD_SYNTHETIC_PROVIDER_PROBE` and `synthetic_only:true`.
No lease/chunk/customer content or user-entered prompt is sent. API responses are
no-store under the existing security middleware.

Redis atomically enforces per-org fixed-window attempts (default **3 / 600s**) and
one outbound probe at a time. Org limiter keys use a keyed digest. Acquisition,
renewal and release use a unique ownership token; refresh immediately before the
bounded call prevents expired/stolen ownership from starting egress. Release is
compare-and-delete, not an unconditional unlock. Redis outages fail closed.
Busy → 409; exhausted window → 429/Retry-After; disabled/unavailable → 503.
Failed admitted attempts count too; no limit refund or automatic paid retry occurs.
This is not a complete account-wide spend cap; Redis resets/new orgs/other provider
clients or signing-key rotation can incur more usage. Set independent upstream budgets and alerts.

`0007_providers` adds durable tenant-scoped `provider_runs`, with a composite
requester/tenant FK and unique tenant/request key. A separate transaction commits
STARTED **before** HTTP egress; final status commits before a success response.
History stores organization/actor, configured and returned model identifiers,
provider/profile, prompt name/version/message SHA-256, temperature/output cap,
actual timestamps/duration when available, reported token usage, response ID,
validated-output hash or fixed safe failure code. No generated text, prompt text,
credentials, document text, upstream bodies or raw exception strings are persisted.
Authorization is checked again before final publication; revoked/inactive actors,
organizations or sessions cannot publish a successful check.

Returned model IDs are **provider-reported metadata**, not proof of model identity.
OpenAI has no separate verified model-version field; `model_version` is explicitly
null, rather than inventing a version from an alias. Token counts, when recorded,
are upstream-reported nonnegative integers with checked totals; not independently
measured billing. Missing/unretained usage stays null, **never zero**. Failures and
malformed responses can still bill and their usage may not be retained. Monetary
pricing, invoicing, full audit and production evaluation are later phases.

Reusing a recorded key never sends it again, including FAILED and STARTED rows.
An existing terminal result returns 200 with `replayed:true`; STARTED returns 202
and means **pending or unknown**, not success. Concurrent overlapping requests can
receive busy 409. A crash, lost client response, or final database failure can leave
STARTED after an upstream charge. There is deliberately no automatic resume. Check
history and upstream account records before explicitly making a **new paid key**.
Idempotency is per organization and retained SQL record, not a guarantee against
an operator deleting/restoring the ledger or reissuing requests under new keys.

The `/providers` UI renders actual safe configuration/history only, requires a
charge acknowledgement, disables mutation retries, retains an uncertain request
key for safe explicit retry and never labels mere key presence as live connectivity.
History refresh performs reads, not model calls. POST client timeout is 75 seconds;
model HTTP timeout defaults 30 seconds (configurable 1–60).

## Upgrade and run

Use Python 3.12 for the supported installation (the sandbox also checked 3.11):

```sh
pip install -e './backend[dev]'
# Activate this environment, configure existing DB/Redis/storage/graph as before.
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Separate terminal:
cd frontend
npm ci
npm run dev -- --host 0.0.0.0
```

From the repository root `python scripts/init_dev_env.py && docker compose up --build`
remains the complete local infrastructure path; Compose already passes `.env` to
the API and workers, so no new provider container is needed. Existing users must
migrate/restart; DB readiness now requires the `phase8` schema marker. Never
rollback a live ledger without preserving/reconciling uncertain paid requests.
Downgrading removes this SQL ledger, not the provider's charges/records.

**Default generation stays off**, even when retrieval/graph are enabled. No key is
required to use earlier functionality. To opt in, set these in the server's private
environment (never paste a real secret into chat or commit it):

```dotenv
LEASEGUARD_LLM_ENABLED=true
LEASEGUARD_LLM_PROVIDER=openai
LEASEGUARD_LLM_URL=https://api.openai.com/v1
LEASEGUARD_LLM_MODEL=gpt-4.1-mini
LEASEGUARD_LLM_TIMEOUT_SECONDS=30
LEASEGUARD_LLM_MAX_OUTPUT_TOKENS=512
LEASEGUARD_LLM_PROBE_LIMIT=3
LEASEGUARD_LLM_PROBE_WINDOW_SECONDS=600
# Inject LEASEGUARD_LLM_API_KEY privately via your approved secret mechanism.
```

Enabled generation requires a nonempty key and HTTPS outside isolated tests.
Keys are distinct from embedding credentials. Operator-selected endpoints are a
trusted configuration boundary, not arbitrary URLs accepted by users. Configure
outbound allowlists, trusted certificates, vendor data/retention policy and
upstream budgets before enabling; no insecure TLS bypass or automatic proxy use
is implemented. Automatic `/api/ready` checks **never** call or bill the LLM.
Readiness of other services does not prove provider availability. Protect secrets
from third-party HTTP/debug logging and apply least-privilege production secret
injection; the shared development env file is not a production secret manager.

## Verification and honest boundaries

```sh
./scripts/check.sh
cd backend
alembic upgrade head --sql
alembic downgrade 0007_providers:base --sql
# Real migrated Postgres/Redis/etc, but model HTTP is still explicitly mocked:
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
# SEPARATE explicit PAID live synthetic test, only in an approved network and with
# the private provider key already configured. Never runs just because a key exists:
LEASEGUARD_RUN_PROVIDER_LIVE=1 LEASEGUARD_PROVIDER_LIVE_ACK=synthetic-paid-probe \
  python -m pytest tests/test_ai_live.py -q
```

The direct paid test is a single adapter contract check outside the API's diagnostic
ledger/Redis admission guard; it does not prove API deployment, independent model
identity, lease accuracy, extraction quality or production readiness. Normal CI
explicitly disables live model testing; HTTP contract tests use labelled mock
transports, not runtime mock providers. A separately opt-in integration test checks
real PG ledger/Redis scripts, tenant limits and concurrency with mocked model HTTP.

Sandbox results: **136 backend passed, 9 skipped; 24 frontend passed**. Ruff,
formatting, mypy (53 modules), TypeScript and production build pass. Reversible
migration execution/model matching pass on SQLite; offline PostgreSQL SQL renders.
No Docker/PostgreSQL/Redis/MinIO/Qdrant/Neo4j servers are available here. Eight live
infrastructure integrations and the explicit paid provider test are skipped.
No credentials were supplied and no real LLM request was performed. This sandbox's
outbound host policy also does not allow provider hosts. **The actual provider and
live infrastructure paths remain unverified**, not successful by mock inference.
Remote CI/browser end-to-end success and production readiness are not claimed.

Stopped after Phase 8. Next, only on approval: Phase 9 typed LangGraph orchestration.
