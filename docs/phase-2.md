# Phase 2 — backend foundation

Historical phase report: [Phase 3](phase-3.md) now supersedes the identity/security
status and adds migration `0002_identity`; readiness now expects the Phase 3 baseline.

## Scope and inspection

Phase 1 contained a runnable React workspace and one FastAPI health endpoint.
No persistence, settings module, migrations, logging policy or readiness check existed.
Plan: add validated settings → lifecycle-managed engine/session factory → initial
migration → bounded dependency checks → request/error infrastructure → tests and docs.
No authentication, business documents, extraction or tenant data was added.

## Implemented

- Pydantic Settings with `LEASEGUARD_` prefix, environment/.env configuration,
  validated async PostgreSQL and Redis URLs, bounded pool sizes and probe timeout.
  `.env` is read relative to the process working directory. URLs use `SecretStr`.
- Async SQLAlchemy engine with connection pooling, pre-ping, hidden SQL parameters,
  per-use transactional sessions, rollback on errors, and lifecycle disposal.
- Async Alembic setup and reversible `0001_foundation` migration. Only the
  non-tenant `system_metadata` table and deployment baseline exist so far.
  No automatic schema creation in API startup; migrations run separately.
- Redis client with timeouts and shutdown cleanup (no jobs or caching yet).
- JSON application logs with server-generated request UUIDs, duration and status.
  URLs, queries, bodies, auth headers and raw exception text are not logged by this
  application middleware. Uvicorn access logs are disabled in Docker; use
  `--no-access-log` locally too. Avoid third-party SQL debug/echo logging.
- Uniform 404/HTTP, 422 and unexpected-500 errors. Validation input is not echoed.
  Format: `{"error":{"code":"...","message":"...","request_id":"..."}}`.
  Every HTTP response includes `X-Request-ID` for correlation.
- `/health` and `/live` report process liveness. `/ready` concurrently checks
  PostgreSQL's migrated baseline and Redis PING, bounded by the configured timeout.
  Both must pass to return 200; otherwise 503 with sanitized per-service status.
  `/api/health`, `/api/live`, `/api/ready` are equivalent proxy-friendly routes.
- UI readiness is separate from API liveness and accurately reports unavailable services.
- Compose PostgreSQL/Redis healthchecks, persistent PostgreSQL volume, one-shot
  migration service, then readiness-gated backend/frontend startup.
- CI service containers, migrations, schema drift check, live integration tests,
  and migration downgrade/upgrade smoke check.

## Run

From the repository root (Docker required):

```sh
docker compose up --build
```

For local Python development, start dependencies first:

```sh
docker compose up -d postgres redis
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]'
cp .env.example backend/.env
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
```

`POSTGRES_PASSWORD` in a root `.env` is for Compose interpolation. Keep its local
value consistent with backend/.env if changing it. Never use development passwords
in production. Existing PostgreSQL volumes retain their original credentials;
changing an environment variable does not rotate database passwords.

```sh
curl -i http://localhost:8000/health
curl -i http://localhost:8000/ready
```

Open `/docs` directly on port 8000 for OpenAPI. Only `/api/*` routes are proxied by
the frontend; direct readiness aliases are on the backend port.

## Test and migration commands

With the virtual environment activated, from `backend/`:

```sh
ruff check .
ruff format --check .
mypy app
python -m pytest -q
alembic upgrade head --sql  # Offline SQL rendering; no database needed
# With real PostgreSQL and Redis running:
alembic upgrade head
alembic check
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
# Disposable development/test database only (destructive):
alembic downgrade base
alembic upgrade head
```

Unit tests cover config, routes, readiness success/failure combinations, sanitized
errors, IDs, JSON logging, metadata, and failed dependency probes. Integration tests
verify live readiness and real transaction rollback. They skip unless opted in;
CI opts in and supplies both services. Downgrading drops the metadata table.

## Verification and boundaries

Sandbox verification: 16 tests passed, 2 live integration tests skipped; Ruff and
mypy passed, offline Alembic migration SQL generated. Python here is 3.11, while
packaging, CI and Docker require 3.12. Docker, PostgreSQL and Redis binaries are not
available here, so live migrations, Compose and integration checks have **not** been
verified locally. CI configuration is added but its remote execution is not claimed.

Qdrant, Neo4j and object storage are not yet connected and are not falsely reported
healthy. Readiness will expand when those dependencies are implemented. The baseline
check detects missing migrations but is not a future-proof full schema audit;
`alembic check` in CI detects drift. No production security or tenant isolation is
claimed; Phase 3 introduces auth and tenant ownership before business data endpoints.

Live preview smoke check through the Vite proxy: `/api/health` returned 200,
`/api/ready` returned 503 with PostgreSQL/Redis marked unavailable (expected in
this sandbox); both included server-generated request IDs. Frontend formatting,
TypeScript checks and production build passed. Compose and CI YAML parsed.
