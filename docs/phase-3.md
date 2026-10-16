# Phase 3 — identity, RBAC and organization isolation

Historical phase report. [Phase 4](phase-4.md) adds documents and object storage;
readiness now checks its migration baseline and private storage as well.

## Inspection and scope

Phase 2 supplied configuration, async sessions, migration tooling, logging and
readiness, but no users, organizations or authentication. Plan: identity schema →
password/token primitives → authenticated tenant context → scoped member APIs →
rotation/revocation → UI → security tests and runbook. Phase 4 document work is not
included. No existing endpoints or workspace functionality were removed.

## Implemented

- Reversible `0002_identity` migration: organizations, users, auth sessions and
  refresh tokens. User/session/token records contain `organization_id`. Composite
  foreign keys prevent a session referencing another organization's user or a
  refresh token referencing another organization's session. Roles/statuses have
  database constraints. Emails are canonicalized and globally unique; this phase
  intentionally models one organization per account, not multi-org memberships.
- Public registration atomically creates a new organization and its first
  `ORG_ADMIN`. It cannot select an existing organization, change the plan, or
  request another role. Registration can be disabled by configuration.
- Argon2id password hashes; 12–128-character creation policy; passwords are never
  returned or logged. Password whitespace is significant. Unknown-account login
  does an equal-cost dummy verification and uses the same 401 message as a bad
  password. Password hashing runs off the async event loop.
- HS256 JWT access tokens (15 minutes by default), validating signature, algorithm,
  issuer, audience, expiry, issue time, token type and required UUID claims. No roles
  are trusted from a client header or JWT: current membership is read from the DB.
- Cryptographically random opaque refresh tokens stored only as SHA-256 digests.
  Rotation is one-time-use; replay revokes the entire session family, including
  otherwise unexpired access tokens. PostgreSQL row locks serialize rotations.
  Session lifetime is fixed (7 days by default), not extended by rotation.
- Access tokens bind user, organization and server-side session. Each authenticated
  request checks live user/organization status, session expiry and revocation.
  Logout revokes that session; member access changes revoke all of that member's
  sessions. Replay revocation is explicitly committed before returning 401.
- Reusable `CurrentPrincipal`/`Principal.organization_id` trusted tenant context and
  default-deny capability map. Tenant/object lookups are scoped to it. Cross-tenant
  objects return 404. Organization headers and body tenant selectors are ignored or
  rejected. `SUPER_ADMIN` is **not** a cross-tenant bypass and cannot be assigned by
  public or tenant APIs. No platform-admin provisioning API is exposed.
- Redis-backed atomic fixed-window throttling for registration, login and refresh,
  keyed by a digest of the socket peer address (no raw IP in the Redis key). Redis
  failure denies those requests with 503, rather than silently bypassing limits.
- Sensitive API responses use `Cache-Control: no-store`. Application request logs
  include confirmed user/organization IDs, not emails, passwords or raw tokens.
- Minimal functional registration, login and settings/member UI. Tokens exist only
  in memory; reload requires login. One shared refresh request handles concurrent
  access expiry, avoiding accidental replay. Identity changes clear query caches.
  Logout clears local state even if offline, with an explicit warning if server
  revocation could not be confirmed. There are no fake successful sign-ins.

## API (`/api/v1`)

| Method | Path | Authorization |
| --- | --- | --- |
| POST | `/auth/register` | Public, throttled; new organization only |
| POST | `/auth/login` | Public, throttled |
| POST | `/auth/refresh` | Refresh secret in JSON body; throttled |
| POST | `/auth/logout` | Active Bearer access token; revokes current session |
| GET | `/auth/me` | Active member |
| GET | `/organizations/current` | Active member, own organization only |
| GET | `/organizations/{id}` | Active member, own organization or 404 |
| GET | `/users` | Own org: ORG_ADMIN, SUPER_ADMIN, AUDITOR; `limit`/`offset` |
| GET | `/users/{id}` | Same read permission, own organization or 404 |
| POST | `/users` | Own org admin; cannot create SUPER_ADMIN |
| PATCH | `/users/{id}` | Own org admin; role/status updates only |

`REVIEWER`, `ANALYST` and `VIEWER` can read their own profile and organization but
cannot administer or list members. `AUDITOR` is read-only for the member directory.
No lease/review permissions are claimed until those modules exist. Admins cannot
alter their own access or a SUPER_ADMIN. Membership updates serialize per org and
re-check the acting admin to prevent two admins concurrently disabling each other.

Bearer tokens go in `Authorization: Bearer ...`; refresh uses
`{"refresh_token":"..."}`. Login uses `{"email":"...","password":"..."}`.
Registration additionally requires `first_name`, `last_name`, `organization_name`,
and `organization_slug` (lowercase alphanumeric words separated by hyphens).
Unknown input fields are rejected. Member creation uses identity fields and `role`.
PATCH accepts `role` and/or `status` (`ACTIVE`/`DISABLED`). Do not put tokens in URLs.
Token responses contain `access_token`, `refresh_token`, `token_type`, `expires_in`.
OpenAPI at backend `/docs` includes schemas, Bearer security and error responses.

## Local configuration and run

Use Python 3.12+, Node **22.12+** (or supported newer LTS), Docker Compose 2.24+.

```sh
# Repository root; creates .env once with a random signing key; never overwrites.
python scripts/init_dev_env.py
docker compose up --build
```

Open `http://localhost:8080/register`. Register a synthetic organization, open
Settings, add a test member, then sign out and log in as that member to check RBAC.
The overview remains public system information; organization/member data is guarded
by backend authentication. Planned business modules remain unimplemented.
Compose loads root `.env` for backend/migrations; DB/Redis container hostnames are
set by Compose. Local demo DB passwords are not production credentials.

Without Docker for the application (services still required):

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e './backend[dev]'
python scripts/init_dev_env.py  # skip if already created
cp .env backend/.env           # do not overwrite your custom configuration
# backend/.env default URLs refer to local ports, as needed for local Python.
docker compose up -d postgres redis
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --no-access-log
# Separate terminal: cd frontend && npm ci && npm run dev
```

If `.env` already exists, the helper refuses to overwrite it. Configure
`LEASEGUARD_JWT_SECRET` manually using at least 32 random bytes, without committing
or sharing it. Development without an explicit key uses a per-process random key:
restarts invalidate access tokens; multiple workers must share an explicit key.
Production refuses startup without an explicit key. Secret values must come from
secure environment injection in production, never from frontend build variables.
Key rotation invalidates existing access tokens; refresh-token sessions remain DB
backed. Incident response should also revoke affected sessions in the database.

Additional configuration (see `.env.example`):

- `LEASEGUARD_JWT_ISSUER` / `LEASEGUARD_JWT_AUDIENCE`
- `LEASEGUARD_ACCESS_TOKEN_SECONDS` (60–3600; default 900)
- `LEASEGUARD_REFRESH_TOKEN_SECONDS` (3600–2592000; default 604800)
- `LEASEGUARD_REGISTRATION_ENABLED` (default true; disable for closed onboarding)
- `LEASEGUARD_AUTH_RATE_LIMIT` / `LEASEGUARD_AUTH_RATE_WINDOW_SECONDS` (30/60)

## Validation

```sh
. .venv/bin/activate
./scripts/check.sh
cd backend
alembic upgrade head --sql
# With migrated PostgreSQL + Redis:
alembic upgrade head
alembic check
LEASEGUARD_RUN_INTEGRATION=1 python -m pytest -q
# Disposable database only: destructive rollback test
alembic downgrade 0001_foundation
alembic upgrade head
```

Verified in the sandbox: **35 backend tests passed, 4 service integration tests
skipped; 3 frontend tests passed**. Ruff, mypy, TypeScript, formatting and production
build checks passed. Offline migration SQL generated. Frontend npm audit reports
zero vulnerabilities after upgrading the test runner to the fixed release.
Backend identity HTTP tests use real SQLite transactions with foreign keys enabled,
not mocked authentication. SQLite is **test-only** and is not accepted by runtime
configuration. It does not establish PostgreSQL locking behavior.

CI supplies PostgreSQL/Redis and enables the integration suite, including concurrent
refresh replay, tenant isolation, revocation and real transaction rollback; it also
checks migrations, schema drift and downgrade/upgrade. These service checks have not
run locally: Docker/PostgreSQL/Redis binaries are absent. Available Python is 3.11;
packaging/CI/Docker target 3.12. Remote CI execution is not claimed. Frontend tests
mock HTTP only inside test files to exercise identity state, concurrent-refresh
single-flight and rejected-refresh cleanup. No browser end-to-end run is claimed.

## Deliberate limitations and security boundary

- Not production-ready: MFA, password reset/change, email verification, invitation
  delivery, SSO, breached-password checks and account recovery are not implemented.
  Admin-created initial passwords require an approved secure handoff; they are
  not emailed or displayed by the application. No seeded admin credentials exist.
- Isolation is enforced in API/service predicates with DB composite constraints;
  PostgreSQL row-level-security policies are not implemented. Future domain queries
  must use the trusted tenant context, not accept tenant IDs from request input.
- No auth cookies or persistent token storage: ambient-cookie CSRF does not apply to
  these Bearer APIs. TLS, XSS prevention/CSP and trusted-proxy configuration still
  matter; in-memory storage does not eliminate XSS risks. Cross-origin auth is not
  enabled; the UI uses the same-origin proxy. Never expose credentials over plain
  HTTP beyond isolated local development.
- The peer-based limiter is not a complete abuse-management system. Behind a proxy,
  users may share a limit; configure trusted proxy handling and edge controls before
  production. Do not blindly enable trust for all forwarded IP headers.
- Immutable security audit events, retention/cleanup of expired sessions, advanced
  rate limiting, security headers, production key lifecycle and hardening arrive
  in later phases. Current logs are operational, not the Phase 14 audit trail.
- Qdrant/Neo4j/object storage and all lease workflows remain planned.

## Changed areas

`backend/app/auth/`; settings, errors, request logs, health and database dependency;
`backend/alembic/versions/0002_identity.py` and metadata registration; auth unit and
PostgreSQL integration tests; `frontend/src/auth.tsx`, `identity.tsx`, auth tests,
workspace/styles; npm/Python dependencies; Compose, CI, `.env.example`, validation
and dev-environment scripts; README, architecture and this runbook.

Phase 3 stops here. Next approval: Phase 4 document management.

Preview smoke checks through the frontend proxy also passed: login/register routes
returned 200, public health returned 200, readiness returned 503 for the absent
services, unauthenticated profile access returned 401, and login returned sanitized
503 while Redis was unavailable. Backend OpenAPI exposes versioned identity routes
and Bearer security. The environment helper was tested in a temporary directory for
random key generation, mode 0600 and refusal to overwrite existing configuration.
